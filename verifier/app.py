import json
import logging
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

import sympy
import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sympy.parsing.latex import parse_latex


logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("verifier")
app = FastAPI(title="Maths step verifier")

NEUTRAL = {"valid": False, "errorType": None}


class VerifyRequest(BaseModel):
    prev: str = ""
    current: str = ""


class AnswerReviewRequest(BaseModel):
    problem: str = ""
    firstLine: str = ""
    firstLineValid: bool | None = None
    finalLine: str = ""


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Any, _error: RequestValidationError) -> JSONResponse:
    if _request.url.path == "/review-answer":
        return JSONResponse(
            status_code=200,
            content={"finalAnswerCorrect": None, "errorType": "unparseable"},
        )
    verdict = {"valid": False, "errorType": "unparseable"}
    log_verdict(None, None, verdict)
    return JSONResponse(status_code=200, content=verdict)


@app.post("/verify")
def verify(request: VerifyRequest) -> dict[str, bool | str | None]:
    try:
        verdict = verify_step(request.prev, request.current)
    except Exception:
        # User input must never turn a symbolic edge case into a service error.
        verdict = {"valid": False, "errorType": "unparseable"}

    log_verdict(request.prev, request.current, verdict)
    return verdict


@app.post("/review-answer")
def review_answer(request: AnswerReviewRequest) -> dict[str, bool | str | None]:
    """Receipt-only check: compare the final line with the original equation."""
    try:
        result = review_final_answer(
            request.problem,
            request.firstLine,
            request.finalLine,
            request.firstLineValid,
        )
    except Exception:
        result = {"finalAnswerCorrect": None, "errorType": "unparseable"}

    logger.info(
        "answer-review %s",
        json.dumps(
            {
                "problem": request.problem,
                "firstLine": request.firstLine,
                "firstLineValid": request.firstLineValid,
                "finalLine": request.finalLine,
                "finalAnswerCorrect": result["finalAnswerCorrect"],
            },
            ensure_ascii=False,
        ),
    )
    return result


def review_final_answer(
    problem: str,
    first_line: str,
    final_line: str,
    first_line_valid: bool | None = True,
) -> dict[str, bool | str | None]:
    final_equation = parse_equation(final_line)
    original_equation = parse_problem_equation(problem)
    # A worded problem needs its setup equation as a symbolic bridge. Only use
    # that bridge when the existing setup check found it faithful.
    if original_equation is None and first_line_valid is True:
        original_equation = parse_equation(first_line)
    if original_equation is None or final_equation is None:
        return {"finalAnswerCorrect": None, "errorType": "unparseable"}

    original_variables = original_equation.free_symbols
    final_variables = final_equation.free_symbols
    if len(original_variables) != 1 or len(final_variables) != 1:
        return {"finalAnswerCorrect": None, "errorType": "unparseable"}

    original_variable = next(iter(original_variables))
    final_variable = next(iter(final_variables))
    original_solutions = equation_solutions(original_equation, original_variable)
    final_solutions = equation_solutions(final_equation, final_variable)

    if isinstance(original_solutions, str) or isinstance(final_solutions, str):
        matches = original_solutions == final_solutions
    else:
        matches = solution_lists_match(original_solutions, final_solutions)
        if not matches:
            matches = rounded_decimal_solution_matches(
                original_solutions, final_equation, final_line, final_variable
            )
    return {"finalAnswerCorrect": bool(matches), "errorType": None}


def parse_equation(source: str) -> sympy.Equality | None:
    cleaned = clean_latex(source)
    if not cleaned:
        return None
    try:
        parsed = parse_latex(cleaned, strict=True)
    except Exception:
        return None
    return parsed if isinstance(parsed, sympy.Equality) else None


def parse_problem_equation(problem: str) -> sympy.Equality | None:
    source = clean_latex(problem).replace("−", "-").replace("–", "-")
    if not source:
        return None

    candidates: list[str] = []
    stripped_instruction = re.sub(
        r"^\s*(?:solve|find|calculate|determine)(?:\s+for\s+[A-Za-z])?\s*:?\s*",
        "",
        source,
        flags=re.IGNORECASE,
    )
    if stripped_instruction != source:
        candidates.append(stripped_instruction)
    if ":" in source:
        candidates.append(source.split(":", 1)[1].strip())
    candidates.append(source)

    for candidate in candidates:
        equation = parse_equation(candidate)
        if equation is not None and len(equation.free_symbols) == 1:
            return equation
    return None


def verify_step(prev_latex: str, current_latex: str) -> dict[str, bool | str | None]:
    prev_source = clean_latex(prev_latex)
    current_source = clean_latex(current_latex)
    if not prev_source or not current_source:
        return NEUTRAL.copy()

    try:
        prev = parse_latex(prev_source, strict=True)
        current = parse_latex(current_source, strict=True)
    except Exception:
        return {"valid": False, "errorType": "unparseable"}

    prev_is_equation = isinstance(prev, sympy.Equality)
    current_is_equation = isinstance(current, sympy.Equality)

    if prev_is_equation and current_is_equation:
        valid = equations_are_equivalent(prev, current, current_source)
    elif not prev_is_equation and not current_is_equation:
        try:
            valid = sympy.simplify(prev - current) == 0
            if not valid:
                decimal = stated_decimal(current_source)
                valid = decimal is not None and exact_value_rounds_to(prev, *decimal)
        except Exception:
            return {"valid": False, "errorType": "unparseable"}
    else:
        valid = False

    return {"valid": bool(valid), "errorType": None if valid else "not_equivalent"}


def equations_are_equivalent(
    prev: sympy.Equality, current: sympy.Equality, current_source: str
) -> bool:
    variables = prev.free_symbols | current.free_symbols
    if not variables:
        return equation_state(prev, variables) == equation_state(current, variables)
    if len(variables) != 1:
        return False

    variable = next(iter(variables))
    prev_solutions = equation_solutions(prev, variable)
    current_solutions = equation_solutions(current, variable)

    if isinstance(prev_solutions, str) or isinstance(current_solutions, str):
        return prev_solutions == current_solutions
    if solution_lists_match(prev_solutions, current_solutions):
        return True
    return rounded_decimal_solution_matches(
        prev_solutions, current, current_source, variable
    )


def rounded_decimal_solution_matches(
    previous_solutions: list[Any],
    current_equation: sympy.Equality,
    current_source: str,
    variable: sympy.Symbol,
) -> bool:
    if len(previous_solutions) != 1:
        return False

    # Rounding is only accepted for an isolated-variable answer such as b=4.57.
    lhs_is_variable = current_equation.lhs == variable and not current_equation.rhs.free_symbols
    rhs_is_variable = current_equation.rhs == variable and not current_equation.lhs.free_symbols
    if not (lhs_is_variable or rhs_is_variable):
        return False

    decimal = stated_decimal(current_source)
    if decimal is None:
        return False
    return exact_value_rounds_to(previous_solutions[0], *decimal)


def stated_decimal(source: str) -> tuple[str, int] | None:
    """Return the one decimal literal and the precision the student wrote."""
    literals = re.findall(r"(?<![\w.])([+-]?\d+\.(\d+))(?![\w.])", source)
    if len(literals) != 1:
        return None
    literal, fractional_digits = literals[0]
    return literal, len(fractional_digits)


def exact_value_rounds_to(exact_value: Any, student_literal: str, places: int) -> bool:
    if getattr(exact_value, "free_symbols", set()) or exact_value.is_real is False:
        return False
    try:
        student_value = Decimal(student_literal)
        quantum = Decimal(1).scaleb(-places)
        with localcontext() as context:
            context.prec = max(50, places + 30)
            evaluated = Decimal(str(sympy.N(exact_value, context.prec)))
            expected = evaluated.quantize(quantum, rounding=ROUND_HALF_UP)
        return student_value == expected
    except (InvalidOperation, ValueError, TypeError):
        return False


def equation_state(equation: sympy.Equality, variables: set[sympy.Symbol]) -> str:
    residual = sympy.simplify(equation.lhs - equation.rhs)
    if residual == 0:
        return "all"
    if not variables or not residual.free_symbols:
        return "none"
    return "unsupported"


def equation_solutions(equation: sympy.Equality, variable: sympy.Symbol) -> str | list[Any]:
    residual = sympy.simplify(equation.lhs - equation.rhs)
    if residual == 0:
        return "all"
    if not residual.has(variable):
        return "none"
    return list(sympy.solve(equation, variable, dict=False))


def solution_lists_match(prev: list[Any], current: list[Any]) -> bool:
    if len(prev) != len(current):
        return False

    unmatched = list(current)
    for candidate in prev:
        match_index = next(
            (index for index, other in enumerate(unmatched) if sympy.simplify(candidate - other) == 0),
            None,
        )
        if match_index is None:
            return False
        unmatched.pop(match_index)
    return not unmatched


def clean_latex(value: str) -> str:
    source = value.strip()
    for opening, closing in (("$$", "$$"), ("$", "$"), (r"\(", r"\)"), (r"\[", r"\]")):
        if source.startswith(opening) and source.endswith(closing):
            return source[len(opening) : -len(closing)].strip()
    return source


def log_verdict(prev: str | None, current: str | None, verdict: dict[str, Any]) -> None:
    logger.info(
        "verdict %s",
        json.dumps({"prev": prev, "current": current, "valid": verdict["valid"]}, ensure_ascii=False),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
