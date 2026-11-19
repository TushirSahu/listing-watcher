import subprocess
import sys


def test_plugin_server_does_not_load_the_agent_stack():
    # the plugin installs without the [agent] extra; importing any of these would crash it
    code = ("import sys, watcher.mcp_server; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'browser_use', 'langgraph', 'pydantic_ai', 'dotenv'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
