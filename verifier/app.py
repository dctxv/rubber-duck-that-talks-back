import json
import logging
import os
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


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Any, _error: RequestValidationError) -> JSONResponse:
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
        valid = equations_are_equivalent(prev, current)
    elif not prev_is_equation and not current_is_equation:
        try:
            valid = sympy.simplify(prev - current) == 0
        except Exception:
            return {"valid": False, "errorType": "unparseable"}
    else:
        valid = False

    return {"valid": bool(valid), "errorType": None if valid else "not_equivalent"}


def equations_are_equivalent(prev: sympy.Equality, current: sympy.Equality) -> bool:
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
    return solution_lists_match(prev_solutions, current_solutions)


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
