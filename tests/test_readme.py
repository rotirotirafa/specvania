"""T38: bilingual front door and accurate installation/safety claims."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADING_PAIRS = (
    ("#", "specvania", "specvania"),
    ("##", "What it is", "O que é"),
    ("##", "Install", "Instalação"),
    ("##", "Play", "Como jogar"),
    ("##", "Python quickstart", "Início rápido em Python"),
    ("##", "SDD ↔ game", "SDD ↔ jogo"),
    ("##", "Model roles and chains", "Papéis e cadeias de modelos"),
    ("##", "Optional desktop app", "Aplicativo desktop opcional"),
    ("##", "Optional Postgres team mode", "Postgres opcional para equipes"),
    ("##", "Safety", "Segurança"),
    ("##", "Add reference prices", "Adicionar preços de referência"),
    ("##", "Real-run GIF", "GIF de uma execução real"),
    ("##", "Credits", "Créditos"),
    ("##", "Contributing", "Como contribuir"),
    ("##", "Sponsor", "Apoie o projeto"),
)


def headings(text: str) -> list[tuple[str, str]]:
    return re.findall(r"^(#{1,6}) (.+)$", text, re.MULTILINE)


def localized_heading(heading: str, name: str) -> str:
    if name == "README.md":
        return heading
    return next(pt for _, en, pt in HEADING_PAIRS if en == heading)


def section(text: str, heading: str, name: str) -> str:
    heading = localized_heading(heading, name)
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_readmes_have_matching_translated_heading_structure() -> None:
    english = (ROOT / "README.md").read_text()
    portuguese = (ROOT / "README.pt-BR.md").read_text()
    assert len(headings(english)) == len(headings(portuguese)) == len(HEADING_PAIRS)
    assert headings(english) == [(level, en) for level, en, _ in HEADING_PAIRS]
    assert headings(portuguese) == [(level, pt) for level, _, pt in HEADING_PAIRS]
    for readme, name in ((english, "README.md"), (portuguese, "README.pt-BR.md")):
        top = readme.split("\n## ", 1)[0]
        assert "[English](README.md)" in top
        assert "[Português brasileiro](README.pt-BR.md)" in top
        for level, en, _ in HEADING_PAIRS:
            if level == "##":
                assert section(readme, en, name).strip()


@pytest.mark.parametrize("name", ["README.md", "README.pt-BR.md"])
def test_readmes_install_commands_release_status_and_links(name: str) -> None:
    readme = (ROOT / name).read_text()
    install = section(readme, "Install", name)
    for command in (
        "uv tool install --compile-bytecode specvania",
        "uv tool install --compile-bytecode .",
        "uvx specvania --version",
        "export NVIDIA_API_KEY=",
        "~/.zshrc",
    ):
        assert command in install
    for term in (
        "PyPI",
        "https://github.com/rotirotirafa/specvania/releases",
    ):
        assert term in install
    assert install.index(
        "https://github.com/rotirotirafa/specvania/releases"
    ) < install.index("uv tool install")
    assert "runtime" in install
    desktop = section(readme, "Optional desktop app", name)
    for term in ("arm64", "x64", "/Applications", "AppImage"):
        assert term in desktop
    for link in (
        "docs/spec.md",
        "docs/tasks.md",
        "CONTRIBUTING.md",
        "THIRD_PARTY_NOTICES.md",
    ):
        assert f"]({link})" in readme
    assert not re.search(r"map viewer only|v0\.2|Original pixel art", readme, re.I)
    introduction = " ".join(section(readme, "What it is", name).split())
    gif = " ".join(section(readme, "Real-run GIF", name).split())
    if name == "README.md":
        assert "Not released yet" in introduction
        assert (
            "PyPI and downloadable builds come with the first release" in introduction
        )
        for text in (introduction, gif):
            assert "demo is synthetic" in text
            assert "a recording of a real run will replace it" in text
        assert "once released" in install.lower()
        assert "unsigned" in desktop and "not notarized" in desktop
        assert "Open Anyway" in desktop
    else:
        assert "Ainda não foi lançado" in introduction
        assert (
            "PyPI e os builds para download chegam com o primeiro lançamento"
            in introduction
        )
        for text in (introduction, gif):
            assert "demo incluída é sintética" in text
            assert "uma gravação de uma execução real vai substituí-la" in text
        assert "após o primeiro lançamento" in install.lower()
        assert "sem assinatura" in desktop and "não notarizados" in desktop
        assert "Abrir Mesmo Assim" in desktop


@pytest.mark.parametrize("name", ["README.md", "README.pt-BR.md"])
def test_readmes_keep_internal_process_outside_visitor_sections(name: str) -> None:
    readme = (ROOT / name).read_text()
    contributor_heading = localized_heading("Contributing", name)
    visitor_sections = "".join(
        part
        for part in re.split(r"(?=^## )", readme, flags=re.MULTILINE)
        if not part.startswith(f"## {contributor_heading}\n")
    )
    assert not re.search(r"T\d{2}", visitor_sections)
    assert not re.search(r"DEV-\d+|\b[DGO]\d+\b", visitor_sections)
    for phrase in (
        "owner's check",
        "reviewer approval",
        "release CI verification",
        "verificação no CI",
        "responsável pelo projeto",
    ):
        assert phrase.casefold() not in visitor_sections.casefold()


@pytest.mark.parametrize(
    ("name", "sequence", "table_terms"),
    [
        (
            "README.md",
            (
                "title",
                "project",
                "idea",
                "Clarify",
                "Save map",
                "Start expedition",
                "room panel",
                "end screen",
            ),
            (
                "World",
                "Area",
                "Room",
                "Door",
                "Room cleared",
                "Boss",
                "Mage / Warrior / Paladin",
                "Stamina",
                "Cooldown",
                "Respawn",
                "Lives",
                "Coins",
                "Monster",
                "Ledger",
            ),
        ),
        (
            "README.pt-BR.md",
            (
                "título",
                "projeto",
                "ideia",
                "Clarify",
                "Salvar mapa",
                "Iniciar expedição",
                "painel da sala",
                "tela final",
            ),
            (
                "Mundo",
                "Área",
                "Sala",
                "Porta",
                "Sala concluída",
                "Chefe",
                "Mago / Guerreiro / Paladino",
                "Stamina",
                "Recarga",
                "Respawn",
                "Vidas",
                "Moedas",
                "Monstro",
                "Registro",
            ),
        ),
    ],
)
def test_readmes_describe_play_and_every_sdd_castle_mapping(
    name: str, sequence: tuple[str, ...], table_terms: tuple[str, ...]
) -> None:
    readme = (ROOT / name).read_text()
    play = section(readme, "Play", name)
    positions = [play.index(term) for term in sequence]
    assert positions == sorted(positions)
    for term in ("Monaco", "1×", "2×", "4×"):
        assert term in play
    mapping = section(readme, "SDD ↔ game", name)
    for term in table_terms:
        assert f"| {term} |" in mapping
    assert "Token budget of a run" in mapping or "Orçamento de tokens" in mapping


@pytest.mark.parametrize("name", ["README.md", "README.pt-BR.md"])
def test_readmes_credit_every_asset_and_distinguish_studio_from_argv(name: str) -> None:
    readme = (ROOT / name).read_text()
    credits = section(readme, "Credits", name)
    for asset, license_name in (
        ("Kenney Tiny Dungeon", "CC0"),
        ("Press Start 2P", "OFL"),
        ("Phaser", "MIT"),
        ("Monaco", "MIT"),
        ("Electron", "MIT"),
    ):
        line = next(
            line for line in credits.splitlines() if line.startswith(f"- {asset}")
        )
        assert license_name in line
    safety = section(readme, "Safety", name)
    for term in (
        "specvania/<run-id>",
        "gates",
        ".env*",
        "*.pem",
        "*.key",
        "id_*",
        "127.0.0.1",
        "Origin",
        "$SHELL -lic",
        "~/.zshrc",
        "run --ui=desktop",
        "--control-token",
        "argv",
    ):
        assert term in safety
    labels = (
        ("Git", "Commands", "Files", "Local UI", "Dock launch", "CLI desktop launch")
        if name == "README.md"
        else (
            "Git",
            "Comandos",
            "Arquivos",
            "Interface local",
            "Abertura pelo Dock",
            "Desktop iniciado pela CLI",
        )
    )
    for label in labels:
        assert f"**{label}:**" in safety
    if name == "README.md":
        assert "key never passes through Electron" in safety
        assert "tokens never go\n  to argv, files or logs" in safety
        assert "still passes the control token" in safety
    else:
        assert "chave nunca passa pelo Electron" in safety
        assert "tokens nunca vão\n  para argv, arquivos ou logs" in safety
        assert "ainda passa o token de" in safety
