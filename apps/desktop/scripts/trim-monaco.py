"""Offline, deterministic trim of the pinned 0.57.0 AMD build in .monaco-src."""

import base64
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / ".monaco-src"
TARGET = ROOT / "src/specvania/ui/static/vendor/monaco"
LANGUAGES = (
    "python",
    "javascript",
    "typescript",
    "markdown",
    "ini",
    "yaml",
    "html",
    "css",
    "shell",
)


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    # Only remove a previous generated output, never the staging source.
    if (TARGET / "vs").exists():
        shutil.rmtree(TARGET / "vs")
    for name in ("LICENSE", "ThirdPartyNotices.txt"):
        shutil.copyfile(SOURCE / name, TARGET / name)
    visited: set[str] = set()

    def copy_module(name: str) -> None:
        if name in visited or name in ("require", "exports", "module"):
            return
        visited.add(name)
        path = SOURCE / (name + ".js")
        body = path.read_text()
        destination = TARGET / (name + ".js")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body)
        match = re.match(r"define\([^,]+,\s*(\[.*?\])", body)
        if match:
            for dependency in json.loads(match[1]):
                if dependency.startswith("."):
                    dependency = str((Path(name).parent / dependency).as_posix())
                    # Normalize AMD relative module ids without accessing filesystem.
                    parts: list[str] = []
                    for part in dependency.split("/"):
                        if part == "..":
                            parts.pop()
                        elif part != ".":
                            parts.append(part)
                    dependency = "/".join(parts)
                copy_module(dependency)

    entry = (SOURCE / "vs/editor/editor.main.js").read_text()
    dependencies = json.loads(re.match(r"define\([^,]+,\s*(\[.*?\])", entry)[1])
    core = next(
        d.removeprefix("../") for d in dependencies if re.match(r"../editor-[\w-]+$", d)
    )
    copy_module("vs/" + core)
    copy_module("vs/loader")
    copy_module("vs/nls.messages-loader")
    contribution = (SOURCE / "vs/basic-languages/monaco.contribution.js").read_text()
    registrations = []
    for language in LANGUAGES:
        registration = re.search(r'i\(\{id:"' + language + r'".*?\}\)', contribution)[0]
        dependency = re.search(r'a\(\["([^"]+)"\]', registration)[1]
        copy_module("vs/" + dependency.removeprefix("../"))
        registrations.append(registration)
    prefix = contribution[: contribution.index("i({id:")]
    destination = TARGET / "vs/basic-languages/monaco.contribution.js"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # JSON has no basic-language tokenizer upstream; use Monarch only, no service.
    json_tokens = (
        't.languages.register({id:"json",extensions:[".json"]});'
        't.languages.setMonarchTokensProvider("json",{tokenizer:{root:['
        r'[/"(?:[^"\\]|\\.)*"/,"string"],'
        r'[/-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/,"number"],'
        r'[/\b(?:true|false|null)\b/,"keyword"],'
        r'[/[{}\[\]]/,"delimiter.bracket"],[/[,:]/,"delimiter"]]}})'
    )
    destination.write_text(
        prefix + ",".join(registrations) + ";" + json_tokens + "}));\n"
    )
    # Keep the core's diff/editor widgets and tokenizers only. The upstream
    # toggleHighContrast chunk also registers IDE contributions (outline,
    # codelens, inlay hints, suggest, code actions and drag/drop). Their service
    # registrations can arrive after tokenizers initialize StandaloneServices;
    # editor options do not prevent constructor injection. Omit the whole chunk
    # so these contributions cannot be instantiated in this read-only viewer.
    (TARGET / "vs/editor").mkdir(exist_ok=True)
    (TARGET / "vs/editor/editor.main.js").write_text(
        'define("vs/editor/editor.main",["exports","require",'
        f'"vs/nls.messages-loader!","../{core}",'
        '"../basic-languages/monaco.contribution"],'
        "function(exports,require,nls,core){Object.assign(exports,core);"
        'globalThis.monaco=core;const link=document.createElement("link");'
        'link.rel="stylesheet";link.href=require.toUrl("vs/editor/editor.main.css");'
        "document.head.appendChild(link);});\n"
    )
    css = (SOURCE / "vs/editor/editor.main.css").read_text()
    font = re.search(r"data:font/ttf;base64,([A-Za-z0-9+/=]+)", css)
    (TARGET / "vs/editor/codicon.ttf").write_bytes(base64.b64decode(font[1]))
    css = css.replace(font[0], "./codicon.ttf")
    (TARGET / "vs/editor/editor.main.css").write_text(css)
    worker = next((SOURCE / "vs/assets").glob("editor.worker-*.js"))
    (TARGET / "vs/assets").mkdir(exist_ok=True)
    shutil.copyfile(worker, TARGET / "vs/assets" / worker.name)
    # The compiled plain worker expects the upstream ready handshake.
    (TARGET / "worker.js").write_text(
        f'importScripts("./vs/assets/{worker.name}");\npostMessage({{type:"vscode-worker-ready"}});\n'
    )
    size = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file())
    if size > 8_000_000:
        raise ValueError(f"Monaco exceeds 8 MB: {size}")
    print(
        f"Monaco 0.57.0: {size} bytes; core, plain worker, "
        "ten tokenizers (TOML via ini)"
    )


if __name__ == "__main__":
    main()
