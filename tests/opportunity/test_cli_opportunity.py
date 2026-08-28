"""O10 - a CLI, contra um PAT_HOME de verdade.

Os comandos rodam por `pat.cli.main(argv)`, e nao chamando a funcao interna:
o que se quer verificar e que o parser liga o comando ao codigo e que o codigo
de saida diz a verdade. Um teste que chamasse `cmd_opp_critic(args)` direto
passaria com o subcomando desligado do parser.

O teste que define o milestone e `test_critic_sai_com_um_quando_ha_achado_duro`:
o codigo de saida e a parte util num script - "esta tese pode ser
apresentada?" tem que ser respondivel sem alguem ler a prosa.
"""

from __future__ import annotations

import tomllib

import pytest

from pat.cli import main
from pat.opportunity import list_workspaces
from tests.semantics import golden_gpa as gpa

AS_OF = "2026-06-30"


@pytest.fixture
def home(tmp_path):
    """PAT_HOME completo: warehouse com o GPA, do ZIP ao gold."""
    from pat.store.bronze import BronzeStore
    from pat.store.db import connect, migrate
    from tests.semantics.conftest import load_zips

    raiz = tmp_path / "home"
    (raiz / "opportunity").mkdir(parents=True)
    conn = connect(raiz / "warehouse.duckdb")
    migrate(conn)
    load_zips(conn, BronzeStore(raiz / "bronze"), gpa.zips())
    conn.close()
    return raiz


def run(home, *argv) -> int:
    return main(["--home", str(home), "opportunity", *argv])


@pytest.fixture
def workspace_id(home, capsys):
    run(home, "init", "--cod-cvm", "14826", "--as-of", AS_OF, "--mandate", "qualidade do resultado")
    capsys.readouterr()
    return list_workspaces(home / "opportunity")[0].workspace_id


# -- init / list ------------------------------------------------------------


def test_init_abre_workspace_e_diz_o_proximo_passo(home, capsys):
    codigo = run(home, "init", "--cod-cvm", "14826", "--as-of", AS_OF)
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "workspace" in saida
    # Um comando que abre algo e nao diz o que fazer em seguida obriga o
    # usuario a ler o `--help` para descobrir que existe um passo seguinte.
    assert "Proximo passo" in saida
    assert len(list_workspaces(home / "opportunity")) == 1


def test_init_de_empresa_desconhecida_falha_com_instrucao(home, capsys):
    codigo = run(home, "init", "--cod-cvm", "99999", "--as-of", AS_OF)
    erro = capsys.readouterr().err

    assert codigo == 1
    assert "nenhuma entidade conhecida" in erro
    assert not list_workspaces(home / "opportunity")


def test_list_mostra_o_que_existe(home, workspace_id, capsys):
    assert run(home, "list") == 0
    saida = capsys.readouterr().out
    assert workspace_id in saida
    assert "CIA BRASILEIRA DE DISTRIBUICAO" in saida


# -- status / chat ----------------------------------------------------------


def test_status_le_o_workspace(home, workspace_id, capsys):
    assert run(home, "status", "--workspace", workspace_id) == 0
    saida = capsys.readouterr().out
    assert workspace_id in saida
    assert "as_of 2026-06-30" in saida


def test_chat_responde_um_turno(home, workspace_id, capsys):
    assert run(home, "chat", "--workspace", workspace_id, "Quanto foi a receita?") == 0
    capturado = capsys.readouterr()

    assert "receita_liquida" in capturado.out
    # A linha de auditoria vai para stderr: quem le a conversa num pipe quer a
    # fala, nao o registro do que mudou.
    assert "queried_engine" in capturado.err


def test_chat_registra_afirmacao_como_hipotese(home, workspace_id, capsys):
    from pat.opportunity import open_workspace

    assert run(home, "chat", "--workspace", workspace_id, "Acho que o moat e escala.") == 0
    estado = open_workspace(home / "opportunity", workspace_id).state

    assert len(estado.hypotheses) == 1
    assert estado.claims == ()


def test_sem_workspace_o_comando_ensina_a_criar_um(home, capsys):
    assert run(home, "status") == 1
    assert "pat opportunity init" in capsys.readouterr().err


def test_com_varios_workspaces_a_cli_pergunta_qual(home, capsys):
    """Escolher o mais recente seria conveniencia que erra em silencio."""
    run(home, "init", "--cod-cvm", "14826", "--as-of", AS_OF)
    run(home, "init", "--cod-cvm", "14826", "--as-of", AS_OF)
    capsys.readouterr()

    assert run(home, "status") == 1
    assert "escolha com --workspace" in capsys.readouterr().err


# -- research ---------------------------------------------------------------


def test_research_decompoe_executa_e_imprime(home, workspace_id, capsys):
    codigo = run(
        home, "research", "--workspace", workspace_id, "--objective", "a margem e a receita"
    )
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "agenda:" in saida
    assert "parou porque:" in saida
    assert "receita_liquida" in saida


def test_research_sem_cobertura_nao_e_falha_do_comando(home, workspace_id, capsys):
    """Travado e o estado correto de quem nao tem o dado.

    Sair com 1 aqui faria um script tratar "faltou cobertura" como "o comando
    quebrou", e as duas coisas pedem reacoes diferentes.
    """
    codigo = run(
        home, "research", "--workspace", workspace_id, "--objective", "o churn de assinantes"
    )
    assert codigo == 0
    assert "parou porque:" in capsys.readouterr().out


# -- critic -----------------------------------------------------------------


def test_critic_sem_hipotese_sai_zero(home, workspace_id, capsys):
    assert run(home, "critic", "--workspace", workspace_id) == 0
    assert "0 objecao(oes)" in capsys.readouterr().out


def test_critic_sai_com_um_quando_ha_achado_duro(home, workspace_id, capsys):
    """O codigo de saida responde "isto pode ser apresentado?" sem ler a prosa."""
    from pat.contracts.opportunity import (
        Actor,
        EvidenceKind,
        EvidenceLink,
        EvidenceLinked,
        HypothesisOpened,
        HypothesisStatus,
        HypothesisStatusChanged,
        HypothesisStrength,
    )
    from datetime import UTC, datetime

    from pat.opportunity import open_workspace

    ws = open_workspace(home / "opportunity", workspace_id)
    ws.apply(
        HypothesisOpened(
            slug="h-escala",
            statement="escala e o moat",
            falsifiers=("margem cair com volume crescendo",),
        ),
        actor=Actor.USER,
    )
    ws.apply(
        EvidenceLinked(
            hypothesis="h-escala",
            link=EvidenceLink(
                kind=EvidenceKind.METRIC,
                ref="receita_liquida@v1|x|2023-12-31|consolidated|2026-06-30",
                note="serie de receita",
                linked_by=Actor.AGENT,
                at=datetime(2026, 6, 30, tzinfo=UTC),
            ),
        ),
        actor=Actor.AGENT,
    )
    # SUPPORTED sem nenhuma tentativa de falsificacao registrada: achado DURO.
    ws.apply(
        HypothesisStatusChanged(
            slug="h-escala",
            status=HypothesisStatus.SUPPORTED,
            strength=HypothesisStrength.MODERATE,
            rationale="a serie de receita e consistente com ganho de escala",
        ),
        actor=Actor.AGENT,
    )

    codigo = run(home, "critic", "--workspace", workspace_id)
    saida = capsys.readouterr().out

    assert codigo == 1
    assert "[DURO]" in saida
    assert "supported_without_falsification" in saida
    assert "remedio:" in saida


# -- valuation --------------------------------------------------------------


MODELO_TOML = """
slug = "base"
currency = "BRL"
horizon_years = 5

[data.revenue-base]
label = "receita FY2023"
value = "100000000"
unit = "BRL"
result_id = "receita_liquida@v1|x|2023-12-31|consolidated|2026-06-30"

[assumptions.revenue-growth]
label = "crescimento de receita"
value = "0.05"
basis = "historical"
rationale = "media dos ultimos tres anos calculada pelo motor"
derived_from = ["receita_liquida@v1|x|2023-12-31|consolidated|2026-06-30"]

[assumptions.ebit-margin]
label = "margem EBIT"
value = "0.06"
basis = "judgment"
rationale = "margem historica, sem recuperacao assumida"

[assumptions.tax-rate]
label = "aliquota efetiva"
value = "0.34"
basis = "judgment"
rationale = "aliquota nominal, sem planejamento"

[assumptions.reinvestment-rate]
label = "reinvestimento"
value = "0.30"
basis = "judgment"
rationale = "capex de manutencao mais giro"

[assumptions.wacc]
label = "custo de capital"
value = "0.14"
basis = "market"
rationale = "custo de capital de varejo alavancado no pais"

[assumptions.terminal-growth]
label = "crescimento na perpetuidade"
value = "0.03"
basis = "judgment"
rationale = "inflacao de longo prazo, sem ganho real"
"""


def test_valuation_sem_modelo_recusa_e_nao_inventa_default(home, workspace_id, capsys):
    """Nao ha premissa default, e a recusa diz por que.

    Um default seria uma escolha de investimento embutida na ferramenta.
    """
    assert run(home, "valuation", "--workspace", workspace_id) == 1
    assert "Nao ha premissa default" in capsys.readouterr().err


def test_valuation_declarada_em_toml_roda(home, workspace_id, tmp_path, capsys):
    caminho = tmp_path / "modelo.toml"
    caminho.write_text(MODELO_TOML, encoding="utf-8")

    codigo = run(home, "valuation", "--workspace", workspace_id, "--declare", str(caminho))
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "modelo base declarado" in saida
    assert "enterprise value" in saida
    assert "perpetuidade" in saida
    # A grade de premissas sai junto do numero: um valor por acao apresentado
    # sozinho e uma precisao que o modelo nao tem.
    assert "wacc = 0.14" in saida


def test_valuation_com_premissa_faltando_diz_qual(home, workspace_id, tmp_path, capsys):
    bruto = tomllib.loads(MODELO_TOML)
    del bruto["assumptions"]["wacc"]
    linhas = ['slug = "parcial"', 'currency = "BRL"', "horizon_years = 5"]
    for slug, dado in bruto["data"].items():
        linhas.append(f"[data.{slug}]")
        linhas.extend(f'{k} = "{v}"' for k, v in dado.items())
    for slug, premissa in bruto["assumptions"].items():
        linhas.append(f"[assumptions.{slug}]")
        for chave, valor in premissa.items():
            if isinstance(valor, list):
                linhas.append(f"{chave} = {valor!r}".replace("'", '"'))
            else:
                linhas.append(f'{chave} = "{valor}"')
    caminho = tmp_path / "parcial.toml"
    caminho.write_text("\n".join(linhas), encoding="utf-8")

    codigo = run(home, "valuation", "--workspace", workspace_id, "--declare", str(caminho))
    saida = capsys.readouterr().out

    assert codigo == 0
    assert "SEM RESULTADO" in saida
    assert "wacc" in saida


# -- thesis -----------------------------------------------------------------


TESE_TOML = """
slug = "gpa-2026"
statement = "o resultado operacional depende de equivalencia patrimonial"
direction = "no_position"
confidence = "moderate"
key_assumptions = ["a equivalencia continua no mesmo nivel"]
falsifiers = ["a equivalencia cair e o EBIT continuar positivo"]
counter_thesis = "a operacao propria melhora e a equivalencia deixa de importar"

[risks.equivalencia]
text = "a equivalencia cair e o EBIT continuar positivo"
severity = "thesis_breaking"
leading_indicator = "resultado das investidas"
"""


def test_thesis_sem_tese_ensina_a_escrever_uma(home, workspace_id, capsys):
    assert run(home, "thesis", "--workspace", workspace_id) == 1
    assert "--draft" in capsys.readouterr().err


def test_thesis_registrada_e_auditada(home, workspace_id, tmp_path, capsys):
    caminho = tmp_path / "tese.toml"
    caminho.write_text(TESE_TOML, encoding="utf-8")

    run(home, "thesis", "--workspace", workspace_id, "--draft", str(caminho))
    saida = capsys.readouterr().out

    assert "tese gpa-2026 registrada" in saida
    assert "NO_POSITION" in saida
    assert "contra-tese:" in saida
    assert "o que a derruba:" in saida
    assert "auditoria:" in saida


def test_thesis_que_esconde_o_que_a_mata_e_recusada_no_contrato(home, workspace_id, tmp_path):
    """Risco THESIS_BREAKING que nao aparece entre os falsificadores.

    A CLI nao repete a regra - ela deixa o contrato falhar. Repetir a checagem
    aqui criaria dois lugares para mante-la, e um deles envelheceria.
    """
    from pydantic import ValidationError

    quebrada = TESE_TOML.replace(
        'falsifiers = ["a equivalencia cair e o EBIT continuar positivo"]',
        'falsifiers = ["nada em particular"]',
    )
    caminho = tmp_path / "quebrada.toml"
    caminho.write_text(quebrada, encoding="utf-8")

    with pytest.raises(ValidationError, match="THESIS_BREAKING"):
        run(home, "thesis", "--workspace", workspace_id, "--draft", str(caminho))
