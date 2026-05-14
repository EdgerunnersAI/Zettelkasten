from __future__ import annotations


def test_add_zettel_route_uses_module_runner():
    source = open("website/api/zettels_routes.py", encoding="utf-8").read()
    assert "website.api.module_runners.summarization" in source
    assert "run_add_zettel_pipeline(" in source


def test_summarization_runner_has_cli_entrypoint():
    source = open("website/api/module_runners/summarization.py", encoding="utf-8").read()
    assert "argparse.ArgumentParser" in source
    assert "if __name__ == \"__main__\"" in source
    assert "run_add_zettel_pipeline(" in source
