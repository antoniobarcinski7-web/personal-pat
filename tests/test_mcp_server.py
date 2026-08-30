"""O PAT como ferramenta do Claude.

O que se afirma aqui nao e que o servidor conversa bem - isso e do modelo do
outro lado. E que as garantias do PAT continuam valendo depois de atravessar a
fronteira MCP: nenhuma ferramenta calcula, `as_of` e obrigatorio, e recusa vira
conteudo nomeado em vez de erro tecnico.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from pat.mcp_server import build_server


@pytest.fixture(scope="module")
def ferramentas():
    server = build_server()
    return {t.name: t for t in asyncio.run(server.list_tools())}


def test_as_ferramentas_esperadas_existem(ferramentas):
    assert set(ferramentas) == {
        "listar_empresas",
        "cobertura",
        "metricas_disponiveis",
        "metrica",
        "serie",
        "buscar_evidencia",
        "plano_de_contas",
    }


def test_nenhuma_ferramenta_aceita_um_valor(ferramentas):
    """A garantia central, conferida por ASSINATURA e nao por comportamento.

    Uma ferramenta que recebesse `valor`, `montante` ou `numerador` seria uma
    ferramenta pela qual um numero inventado pelo modelo entraria no sistema -
    e ela nao existe porque nao da para escrever, nao porque alguem lembra de
    nao chamar.
    """
    proibidos = {"valor", "value", "montante", "numerador", "denominador", "total"}
    for nome, ferramenta in ferramentas.items():
        propriedades = set(ferramenta.input_schema.get("properties", {}))
        assert not (propriedades & proibidos), (
            f"{nome} aceita {propriedades & proibidos}: uma ferramenta que recebe "
            "numero e por onde o modelo devolve ao sistema um valor que ele mesmo "
            "produziu."
        )


def test_toda_leitura_de_fato_exige_as_of(ferramentas):
    """Sem default de "hoje".

    Um default silencioso faria a mesma pergunta responder coisas diferentes em
    dias diferentes, e ninguem saberia por que - o `as_of` e o que mantem uma
    resposta reproduzivel.
    """
    for nome in ("cobertura", "metrica", "serie", "buscar_evidencia", "plano_de_contas"):
        esquema = ferramentas[nome].input_schema
        assert "as_of" in esquema.get("required", []), (
            f"{nome} nao exige as_of: a consulta pode vazar conhecimento posterior "
            "a data analisada"
        )


def test_as_ferramentas_de_catalogo_nao_exigem_as_of(ferramentas):
    """`metricas_disponiveis` e `listar_empresas` descrevem o SISTEMA, e nao o
    que se sabia numa data. Exigir `as_of` ali seria cerimonia sem conteudo."""
    for nome in ("listar_empresas", "metricas_disponiveis"):
        assert not ferramentas[nome].input_schema.get("required")


def test_toda_ferramenta_tem_descricao_util(ferramentas):
    """A descricao E o prompt: e o unico lugar onde cabe dizer que `secoes` so
    aceita valor vindo de `cobertura`, ou que `serie` mostra reversao que duas
    chamadas de `metrica` escondem."""
    for nome, ferramenta in ferramentas.items():
        assert ferramenta.description and len(ferramenta.description) > 60, nome


def test_a_recusa_vira_conteudo_e_nao_excecao():
    """Do outro lado ha um modelo montando resposta.

    Uma excecao viraria "a ferramenta falhou" - que se le como problema
    tecnico, quando a informacao e sobre a empresa ou sobre a cobertura.
    """
    from pat.mcp_server import _resultado

    class RecusaFalsa:
        metric = "ebitda"
        metric_version = "v1"
        reason = type("R", (), {"value": "missing_fact_as_of"})()
        message = "nada conhecido nesta data"
        concept_id = "revenue_net"
        remedy = "ingira o exercicio"

        from datetime import date as _d

        period_end = _d(2025, 12, 31)

    saida = _resultado(RecusaFalsa())

    assert saida["disponivel"] is False
    assert saida["motivo"] == "missing_fact_as_of"
    assert saida["conceito_faltante"] == "revenue_net"
    assert saida["remedio"]


def test_o_valor_sai_legivel_e_exato_ao_mesmo_tempo():
    """O legivel existe porque dez casas decimais escondem a ordem de grandeza.
    O exato existe porque o legivel e arredondado, e numero arredondado que
    volta ao sistema e por onde uma aproximacao entra num calculo."""
    from datetime import date
    from decimal import Decimal

    from pat.contracts.semantics import Dimension
    from pat.mcp_server import _resultado

    class ResultadoFalso:
        metric = "receita_liquida"
        metric_version = "v1"
        value = Decimal("45183036000.0000000000")
        dimension = Dimension.MONEY
        currency = "USD"
        period_end = date(2025, 12, 31)
        period_start = date(2025, 1, 1)
        period_type = type("P", (), {"value": "year"})()
        as_of = date(2026, 8, 29)
        knowledge_date = date(2026, 1, 23)
        fidelity = type("F", (), {"value": "exact"})()
        scope = type("S", (), {"value": "consolidated"})()
        mapping_confirmed = True
        entity_id = "us:cik:0001065280"

    saida = _resultado(ResultadoFalso())

    assert saida["valor"] == "US$ 45,18 bi"
    assert saida["valor_exato"] == "45183036000.0000000000"
    # O arredondado e string, sempre: nao ha caminho de volta ao motor.
    assert isinstance(saida["valor"], str)


def test_a_fidelidade_e_o_mapeamento_conferido_sempre_saem():
    """As duas coisas que distinguem um numero conferido de um plausivel.

    Omiti-las quando sao "boas" faria a ausencia virar sinal, e um dia alguem
    leria o silencio como confirmacao.
    """
    from pat.mcp_server import _resultado

    campos = set(
        _resultado(
            type(
                "R",
                (),
                {
                    "metric": "x",
                    "metric_version": "v1",
                    "reason": type("R", (), {"value": "z"})(),
                    "message": "m",
                    "concept_id": None,
                    "period_end": __import__("datetime").date(2025, 1, 1),
                },
            )()
        )
    )
    assert "motivo" in campos

    esquema = inspect.getsource(_resultado)
    assert '"fidelidade"' in esquema
    assert '"mapeamento_conferido"' in esquema


def test_o_servidor_declara_as_regras_para_quem_o_usa():
    """As `instructions` sao o unico lugar onde o servidor diz ao modelo do
    outro lado que ele NAO deve calcular. Sem isso, a primeira coisa que um
    modelo faz com dois numeros e soma-los."""
    server = build_server()
    assert "NUNCA calcule" in server.instructions
    assert "as_of" in server.instructions


def test_metricas_disponiveis_traz_a_definicao_de_cada_uma():
    """`ebit@v1` inclui equivalencia patrimonial e `divida_bruta@v1` exclui
    arrendamento. Duas decisoes que mudam o numero e que precisam viajar junto
    com o nome da metrica."""
    server = build_server()
    resposta = asyncio.run(server.call_tool("metricas_disponiveis", {}))
    catalogo = [json.loads(bloco.text) for bloco in resposta.content]

    assert catalogo
    for metrica in catalogo:
        # `ref` tem que ser a string que as outras ferramentas aceitam. Um
        # objeto {name, version} obrigaria quem chama a remontar "nome@versao"
        # a mao, e um dia alguem remontaria errado.
        assert "@" in metrica["ref"]
        assert metrica["definicao"]
        assert metrica["por_que_assim"]
