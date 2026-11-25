"""ATTa result verifier — copied into generated packages as `_atta_verify.py`. STANDARD LIBRARY ONLY.

Independent of the integration's own code (a plugin/connector never verifies itself): it imports nothing from the
package and checks the recorded output against the APPROVED output schema embedded at generation time."""
from _atta_schemas import OUTPUT_SCHEMAS

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(value, t):
    if t is None:
        return True
    if isinstance(t, list):
        return any(_type_ok(value, x) for x in t)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _TYPES.get(t)
    return True if py is None else isinstance(value, py)


def check(schema, output):
    if not isinstance(output, dict):
        return ["output is not an object"]
    problems = []
    for k in schema.get("required") or []:
        if k not in output:
            problems.append("missing required field %r" % k)
    props = schema.get("properties") or {}
    for k, v in output.items():
        if k in props and isinstance(props[k], dict) and not _type_ok(v, props[k].get("type")):
            problems.append("field %r has the wrong type" % k)
        elif k not in props and schema.get("additionalProperties") is False:
            problems.append("unexpected field %r" % k)
    return problems


def verify_with(tool, output):
    schema = OUTPUT_SCHEMAS.get(tool)
    problems = ["no approved output schema for tool %r" % tool] if schema is None else check(schema, output)
    return {"verified": not problems, "problems": problems[:5], "verifier": "atta-integration-verify/1"}
