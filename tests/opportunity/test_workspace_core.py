"""O0 - o workspace existe, persiste, e sobrevive ao processo.

O teste que da nome ao milestone e `test_estado_sobrevive_ao_processo`: ele
grava num subprocesso, deixa o interpretador morrer e reabre em outro. Um
teste que apenas fechasse e reabrisse o objeto na mesma sessao provaria muito
menos - cache em memoria passaria por persistencia.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest

from pat.contracts.opportunity import (
    Actor,
    AsOfAdvanced,
    CoverageRefreshed,
    CoverageSnapshot,
    MandateSet,
    NoteAdded,
    TitleSet,
    WorkspaceArchived,
    WorkspaceReopened,
    WorkspaceStatus,
)
from pat.opportunity import (
    FoldError,
    Workspace,
    WorkspaceArchivedError,
    WorkspaceNotFound,
    create_workspace,
    list_workspaces,
    new_workspace_id,
    open_workspace,
)
from pat.opportunity.journal import Journal, JournalCorrupt
from tests.opportunity.conftest import AS_OF, CREATED_AT


def test_criacao_grava_cabecalho(root, gpa_profile):
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)

    estado = ws.state
    assert estado.workspace.company == gpa_profile
    assert estado.workspace.as_of == AS_OF
    assert estado.workspace.status is WorkspaceStatus.OPEN
    assert estado.workspace.created_at == CREATED_AT
    assert estado.workspace.updated_at == CREATED_AT
    assert estado.workspace.seq == 1
    assert estado.entity_id == "br:cnpj:47508411000156"
    assert estado.workspace.jurisdiction == "BR"


def test_as_duas_jurisdicoes_convivem(root, gpa_profile, intel_profile):
    """O criterio de aceitacao da O0: BR e US, no mesmo lugar, sem colisao."""
    br = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    us = create_workspace(
        root, company=intel_profile, as_of=AS_OF, created_at=CREATED_AT + timedelta(hours=1)
    )

    assert br.workspace_id != us.workspace_id
    listados = list_workspaces(root)
    assert len(listados) == 2
    # Mais recente primeiro.
    assert [e.workspace.jurisdiction for e in listados] == ["US", "BR"]
    assert {e.workspace.company.local_id("cod_cvm") for e in listados} == {"14826", None}
    assert {e.workspace.company.local_id("cik") for e in listados} == {"0000050863", None}


def test_atualizacao_e_evento_novo_nao_sobrescrita(root, gpa_profile):
    ws = create_workspace(
        root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT, mandate="primeiro"
    )
    ws.apply(MandateSet(mandate="segundo"), actor=Actor.USER)
    ws.apply(TitleSet(title="GPA 2025"), actor=Actor.USER)

    assert ws.state.workspace.mandate == "segundo"
    assert ws.state.workspace.title == "GPA 2025"
    assert ws.state.workspace.seq == 3

    # O mandato antigo continua no diario: a mudanca de ideia e auditavel.
    eventos = ws.journal.read()
    assert eventos[0].body.mandate == "primeiro"
    assert eventos[1].body.mandate == "segundo"
    assert [e.seq for e in eventos] == [1, 2, 3]


def test_reabrir_produz_o_mesmo_estado(root, gpa_profile):
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(NoteAdded(text="o negocio e varejo alimentar"), actor=Actor.USER)
    ws.apply(NoteAdded(text="checar mix de bandeiras"), actor=Actor.AGENT)

    reaberto = open_workspace(root, ws.workspace_id)

    assert reaberto.state == ws.state
    assert [n.text for n in reaberto.state.notes] == [
        "o negocio e varejo alimentar",
        "checar mix de bandeiras",
    ]
    assert [n.actor for n in reaberto.state.notes] == [Actor.USER, Actor.AGENT]


def test_estado_sobrevive_ao_processo(root, gpa_profile, tmp_path):
    """Escreve num processo, mata o interpretador, le em outro.

    E a diferenca entre persistir e lembrar.
    """
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    wid = ws.workspace_id

    escrita = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pat.opportunity import open_workspace\n"
                "from pat.contracts.opportunity import Actor, NoteAdded\n"
                f"ws = open_workspace({str(root)!r}, {wid!r})\n"
                "ws.apply(NoteAdded(text='escrito por outro processo'), actor=Actor.AGENT)\n"
                "print(ws.state.workspace.seq)\n"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path.parent),
        env={"PYTHONPATH": _src_dir(), "PATH": "/usr/bin:/bin"},
    )
    assert escrita.returncode == 0, escrita.stderr
    assert escrita.stdout.strip() == "2"

    reaberto = open_workspace(root, wid)
    assert [n.text for n in reaberto.state.notes] == ["escrito por outro processo"]
    assert reaberto.state.workspace.seq == 2


def _src_dir() -> str:
    import pat

    from pathlib import Path

    return str(Path(pat.__file__).parent.parent)


def test_as_of_anda_para_frente(root, gpa_profile):
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(AsOfAdvanced(as_of=date(2025, 9, 30), reason="saiu o 3T25"), actor=Actor.USER)
    assert ws.state.as_of == date(2025, 9, 30)


def test_as_of_para_tras_e_recusado_e_nao_chega_ao_disco(root, gpa_profile):
    """A recusa acontece antes da gravacao.

    Se o evento entrasse no diario e so a leitura reclamasse, o workspace
    ficaria impossivel de abrir - o erro se tornaria permanente.
    """
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)

    with pytest.raises(FoldError, match="as_of andando para tras"):
        ws.apply(AsOfAdvanced(as_of=date(2024, 1, 1), reason="voltar"), actor=Actor.USER)

    assert ws.state.as_of == AS_OF
    assert len(ws.journal.read()) == 1
    assert open_workspace(root, ws.workspace_id).state.as_of == AS_OF


def test_cobertura_entra_como_evento(root, gpa_profile):
    cobertura = CoverageSnapshot(
        workspace_sha256="a" * 64,
        state="draft",
        as_of=AS_OF,
        facts=120,
        period_ends=(date(2023, 12, 31), date(2024, 12, 31)),
        metrics_available=("ebitda@v1",),
        missing_concepts=("net_debt",),
        documents=7,
        units_indexed=340,
        extraction_failures=1,
        gaps=(("no_documents", "rode `pat docs sync`"),),
        refreshed_at=CREATED_AT,
    )
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(CoverageRefreshed(coverage=cobertura), actor=Actor.ENGINE)

    assert ws.state.workspace.coverage == cobertura
    # Cobertura que so mostra o que existe mente sobre si mesma.
    assert ws.state.workspace.coverage.missing_concepts == ("net_debt",)
    assert ws.state.workspace.coverage.extraction_failures == 1


def test_arquivado_recusa_escrita_ate_reabrir(root, gpa_profile):
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(WorkspaceArchived(reason="tese entregue"), actor=Actor.USER)
    assert ws.state.workspace.status is WorkspaceStatus.ARCHIVED

    with pytest.raises(WorkspaceArchivedError):
        ws.apply(NoteAdded(text="mais uma ideia"), actor=Actor.USER)

    ws.apply(WorkspaceReopened(), actor=Actor.USER)
    ws.apply(NoteAdded(text="mais uma ideia"), actor=Actor.USER)
    assert ws.state.workspace.status is WorkspaceStatus.OPEN
    assert len(ws.state.notes) == 1


def test_workspace_inexistente(root, gpa_profile):
    with pytest.raises(WorkspaceNotFound):
        open_workspace(root, "0" * 16)


@pytest.mark.parametrize("ruim", ["../../etc", "nao-hex", "ABCDEF0123456789", "abc"])
def test_workspace_id_invalido_nunca_vira_caminho(root, ruim):
    with pytest.raises(ValueError):
        open_workspace(root, ruim)


def test_id_emitido_e_hex_de_16(gpa_profile):
    wid = new_workspace_id(created_at=CREATED_AT, nonce=b"x" * 16)
    assert len(wid) == 16 and all(c in "0123456789abcdef" for c in wid)
    # Determinstico dados instante e nonce - e so por isso o teste pode fixar.
    assert wid == new_workspace_id(created_at=CREATED_AT, nonce=b"x" * 16)


def test_lista_vazia_quando_a_raiz_nao_existe(tmp_path):
    assert list_workspaces(tmp_path / "nunca-criada") == ()


def test_diario_truncado_e_erro_nomeado(root, gpa_profile):
    """Buraco na sequencia para a leitura.

    O estado dobrado de um diario com buraco pareceria um estado legitimo mais
    antigo, e ninguem notaria a perda.
    """
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT)
    ws.apply(NoteAdded(text="um"), actor=Actor.USER)
    ws.apply(NoteAdded(text="dois"), actor=Actor.USER)

    caminho = ws.journal.path
    linhas = caminho.read_text(encoding="utf-8").splitlines(keepends=True)
    caminho.write_text(linhas[0] + linhas[2], encoding="utf-8")

    with pytest.raises(JournalCorrupt, match="esperado 2"):
        open_workspace(root, ws.workspace_id)


def test_diario_ilegivel_e_erro_nomeado(root):
    caminho = root / ("b" * 16) / "journal.jsonl"
    caminho.parent.mkdir(parents=True)
    caminho.write_text("{nao e json\n", encoding="utf-8")

    with pytest.raises(JournalCorrupt, match="ilegivel"):
        open_workspace(root, "b" * 16)


def test_evento_antes_da_criacao_e_recusado(root):
    caminho = root / ("c" * 16) / "journal.jsonl"
    caminho.parent.mkdir(parents=True)
    journal = Journal(caminho)
    journal.append(NoteAdded(text="orfa"), actor=Actor.USER, at=CREATED_AT)

    with pytest.raises(FoldError, match="antes da criacao"):
        Workspace("c" * 16, journal)


def test_dois_workspaces_sobre_a_mesma_empresa(root, gpa_profile):
    """Mandatos diferentes sobre a mesma empresa nao colidem.

    E por isso que `workspace_id` nao e derivado do `entity_id`.
    """
    a = create_workspace(
        root, company=gpa_profile, as_of=AS_OF, created_at=CREATED_AT, mandate="long"
    )
    b = create_workspace(
        root,
        company=gpa_profile,
        as_of=AS_OF,
        created_at=CREATED_AT + timedelta(seconds=1),
        mandate="short",
    )
    assert a.workspace_id != b.workspace_id
    assert {e.workspace.mandate for e in list_workspaces(root)} == {"long", "short"}


def test_at_do_evento_nao_e_o_as_of(root, gpa_profile):
    """Duas datas diferentes que nunca se confundem: quando foi gravado, e de
    quando e a visao do mundo."""
    gravado_em = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    ws = create_workspace(root, company=gpa_profile, as_of=AS_OF, created_at=gravado_em)
    assert ws.state.workspace.created_at.date() == date(2026, 1, 15)
    assert ws.state.as_of == AS_OF
