import lark


def test_lark_is_this_project_not_lark_parser():
    # `lark` is also the PyPI name of the lark-parser library. Fail loudly if it ever shadows us.
    assert hasattr(lark, "__version__") and not hasattr(lark, "Lark")
    assert lark.__file__.endswith("lark/__init__.py")
