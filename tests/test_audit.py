"""audit 一致性审计的离线单元测试。"""
from mcp_bench.audit import audit_schema, ast_extract, _diff, _returns_section, _top_level_keys


class _Tool:
    def __init__(self, name, description="", inputSchema=None, outputSchema=None, annotations=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema
        self.outputSchema = outputSchema
        self.annotations = annotations


def test_audit_schema_flags_problems():
    tools = [
        _Tool("empty_desc", description=""),
        _Tool("short_desc", description="太短"),
        _Tool("no_param_desc", description="这是一个足够长的描述来说明这个工具",
              inputSchema={"properties": {"path": {"type": "string"}}, "required": ["path"]},
              outputSchema={"type": "object"}, annotations={"readOnlyHint": True}),
        _Tool("good", description="这是一个足够长的描述来说明这个工具",
              inputSchema={"properties": {"path": {"type": "string", "description": "路径"}},
                           "required": ["path"]},
              outputSchema={"type": "object"}, annotations={"readOnlyHint": True}),
    ]
    issues = audit_schema(tools)
    names = {i["name"] for i in issues}
    assert "empty_desc" in names
    assert "short_desc" in names
    assert "no_param_desc" in names
    assert "good" not in names


def test_returns_section_extraction():
    doc = """做某事。

    Args:
        x: 参数

    Returns:
        {"success": True, "project": {...}, "components": {"total": 5}}

    Raises:
        ...
    """
    sec = _returns_section(doc)
    assert '"success"' in sec
    assert '"components"' in sec
    assert "Args" not in sec


def test_diff():
    missing, extra = _diff(["success", "project", "components"], ["success", "project"])
    assert missing == ["components"]
    assert extra == []
    missing, extra = _diff(["success"], ["success", "details"])
    assert missing == []
    assert extra == ["details"]


def test_top_level_keys_excludes_nested():
    doc = ('{"success": True, "project": {"project_id": "...", "name": "x"}, '
           '"components": {"total": 55, "by_type": {"HGC367": 16}}}')
    keys = _top_level_keys(doc)
    assert keys == {"success", "project", "components"}
    assert "project_id" not in keys
    assert "total" not in keys
    assert "HGC367" not in keys


def test_ast_extract(tmp_path):
    src = tmp_path / "svc.py"
    src.write_text('''
import mcp

@mcp.tool()
def get_thing(project_path: str) -> dict:
    """获取概览。

    Args:
        project_path: 路径。

    Returns:
        {"success": True, "project": {...}, "components": {"total": 5}}
    """
    return {"success": True, "project": {}, "extra_field": 1}
''', encoding="utf-8")
    rows = ast_extract(str(tmp_path))
    assert "get_thing" in rows
    r = rows["get_thing"]
    assert "success" in r["declared_keys"]
    assert "components" in r["declared_keys"]
    assert "success" in r["return_keys"]
    assert "extra_field" in r["return_keys"]
    assert r["opaque"] is False
    missing, extra = _diff(r["declared_keys"], r["return_keys"])
    assert "components" in missing      # 声明了 components 但 return 里没有
    assert "extra_field" in extra       # return 里有 extra_field 但没声明
