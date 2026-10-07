# Resultados — 2026-10-07

A autenticação OpenMarkets e OpenAI funciona com as chaves locais, sem imprimir seus valores. O nome correto é `OPENMARKETS_API_KEY`. A validação foi conduzida também por agentes **GPT-6 Luna, esforço máximo**, conforme solicitado. As chamadas LLM da aplicação usaram o modelo OpenAI confirmado `gpt-4.1-mini`; o modelo/provedor padrão do projeto não foi alterado.

## Análise e backtest reais confirmados

Comando efetivamente executado, na branch `main` do checkout existente:

```bash
cd /home/caio/repos/ai-hedge-fund-br
export HEDGE_FUND_HOME="$PWD/scratchpad/openmarkets/runtime"
.venv/bin/python -m hedge_fund.validate_openmarkets \
  --universe PETR4 --benchmark BOVA11 \
  --start 2024-06-03 --end 2024-06-14 \
  --out outputs/openmarkets-live-validated \
  --mandate hedge_fund/fund/example.yaml --model gpt-4.1-mini
```

Resultado: **exit 0**; 26 requests OpenMarkets; oito capturas iniciais e 18 respostas adicionais paginadas guardadas sem a chave. PETR4 e BOVA11 têm dez barras nesse intervalo e metadata de fechamento ajustado confirmada. O snapshot PETR4 contém 18 períodos utilizáveis, ROE médio 0,2628, margem líquida média 0,1865 e CAGR BVPS 0,1523; EPS YoY permanece nulo, pois EPS trimestral não foi inventado como TTM. As observações sem entrega conhecida, anteriores a parte do histórico de 2019, foram excluídas do corte histórico.

O replay usou os três analistas Graham/Buffett/Munger do mandato, com seis sinais reais e **zero abstenções**, dez sessões observadas, dois ciclos e duas ordens no SimBroker. Fonte: `openmarkets`, moeda: `BRL`, confiabilidade: `exploratory`.

| Resultado da janela | Valor |
|---|---:|
| Capital inicial | R$ 100.000,00 |
| NAV final | R$ 98.362,22 |
| Retorno do fundo | −1,6378% |
| Retorno BOVA11 | −1,8744% |
| Excesso sobre benchmark | +0,2366 ponto percentual |
| Drawdown máximo | 1,7858% |
| Ordens simuladas | 2 |

Auditoria aritmética independente recalculou caixa, posições, NAV e correspondência fill/mark em todas as dez sessões, sem divergência. Os fills foram 886 unidades em 04/06 e oito em 11/06, no fechamento seguinte às decisões; são unidades do proxy ajustado de pesquisa, não ordens enviadas à corretora. O caixa final é R$ 74.783,94, mais 894 unidades × fechamento ajustado de R$ 26,37391090.

Artefatos locais, ignorados pelo Git:

- `outputs/openmarkets-live-validated/report.json`: checks, contagem de requests e escopo.
- `outputs/openmarkets-live-validated/PETR4-snapshot.json`: estrutura e cálculos fundamentais.
- `outputs/openmarkets-live-validated/backtest.json`: curva, sinais, decisões, fills e limitações.
- `outputs/openmarkets-live-validated/calculation-audit.json`: recomputação independente.
- `outputs/openmarkets-live-validated/requests/`: páginas autenticadas redigidas.
- `outputs/openmarkets-live/price-adjustments.json`: conferência de proventos e fatores.

`complete=true` no relatório significa que os checks solicitados nessa invocação passaram. **`migration_complete=false`** permanece explícito: notícias REST e anúncios/consenso necessários ao PEAD não são cobertos. Esse campo não declara todos os fluxos antigos migrados.

## CLI com universo ampliado e demais contratos reais

O CLI público também terminou com **exit 0**:

```bash
cd /home/caio/repos/ai-hedge-fund-br
export HEDGE_FUND_HOME="$PWD/scratchpad/openmarkets/runtime"
.venv/bin/python -m hedge_fund.run --model gpt-4.1-mini backtest \
  hedge_fund/fund/example.yaml --universe PETR4,VALE3,ITUB4 \
  --start 2024-06-03 --end 2024-06-14 \
  --out outputs/openmarkets-cli-backtest.json
```

Três ações com preços e fundamentos confirmados para esse replay; benchmark BOVA11; dez sessões, 18 sinais dos analistas, zero abstenções, dois ciclos e duas ordens. Os sinais podem reutilizar cache de chamadas LLM reais; não são 18 novas chamadas faturadas. Apenas PETR4 recebeu posição, portanto NAV e métricas finais coincidem com o primeiro replay. Resultado: outputs/openmarkets-cli-backtest.json.

O validador estendido também terminou com **exit 0**, seis requests e quatro checks aprovados: get_earnings (latest 2026-06-30, nove campos trimestrais e 13 anuais presentes), get_earnings_history (12 períodos ITR/DFP), get_insider_trades (60 movimentos datados), get_proventos (seis eventos Jan–Jun/2024). São views contábeis atuais, não anúncios históricos. Report: outputs/openmarkets-extended/report.json.

A falha de insiders foi corrigida a partir da resposta completa: 266 linhas, das quais 194 eram `operacao=sem_mov`, sem dia, quantidade, preço ou volume, e representam saldos mensais. Não foram convertidas em transações. Doze movimentos tinham data posterior ao cutoff; 60 ficaram no intervalo. Uma compra realmente sem data continua causando erro, sem descarte silencioso. O ticker identifica a companhia reportante (`ticker_scope=reporting_company`), não garante o ticker/classe do instrumento negociado. Individualização e publicação permanecem ausentes.

## Regressões e correções originadas pela API real

A suite completa final passou **697 testes e ignorou 12 opt-in live**, em 27,60 s. Separadamente, todos os **12 testes reais opt-in passaram**, sem skips, em 140,71 s: preços, métricas, perfil e coverage de PETR4, VALE3 e ITUB4 entre 2025-01-02 e 2025-01-31. Logs: outputs/openmarkets-live-validated/pytest-unit.txt e pytest-live.txt. git diff --check passou.

Correções efetivamente implementadas:

- OHLC bruto escalado pelo fator adjusted/raw close; conserva volume original, vendor e fonte HTTP.
- Métricas já normalizadas em BRL não recebem outro fator ×1.000; demonstrativos MIL recebem esse fator.
- Histórico usa `market_date_mode=statement_date`. O filtro `metric_keys` rejeita três chaves emitidas pelo próprio histórico; consulta sem esse filtro e seleção local resolvem HTTP 422 sem mudar de fonte.
- Indisponibilidade explícita `available=false` preserva nulo mesmo com motivo extensível; motivos desconhecidos com disponibilidade verdadeira continuam falhando.
- `source_delivery_date` corta entregas posteriores e registros sem data são excluídos; cache também respeita entrega, sem alegar arquivo PIT completo.
- EPS/FCF por ação YTD não viram trimestre isolado nem TTM; CAGR usa duração real e EPS YoY exige comparação anual correta.
- BOVA11 exige quotes, não perfil/coverage de empresa que respondem 404 para esse ETF.
- Erros, redirects, falha de página e cursor truncado sem continuação não viram histórico parcial ou vazio silencioso.
- PEAD/event study recusam capacidade ausente antes da consulta; não executam uma estratégia fictícia com zero operações.

As fixtures dos testes unitários são identificadas como sintéticas ou exemplos minimizados das respostas capturadas. Os resultados reais acima vêm da API autenticada e do LLM real, e não dos mocks.

## Reproduzir testes

```bash
cd /home/caio/repos/ai-hedge-fund-br
export HEDGE_FUND_HOME="$PWD/scratchpad/openmarkets/runtime"
.venv/bin/python -m pytest hedge_fund -q --tb=short
OPENMARKETS_LIVE_TESTS=1 .venv/bin/python -m pytest hedge_fund/data/test_client.py -v
.venv/bin/python -m hedge_fund.validate_openmarkets_extended \
  --ticker PETR4 --start 2024-01-01 --end 2024-06-14 \
  --out outputs/openmarkets-extended
git diff --check
```

A `.venv` usa Python 3.12.3. Preparação do ambiente está em [openmarkets-backtesting.md](openmarkets-backtesting.md). Na execução Codex, a suite completa precisou de escalonamento aprovado porque o sandbox bloqueava o encerramento de threads asyncio; um programa mínimo independente reproduziu o problema. No terminal local usual, os comandos não usam esse sandbox.

`.env` continua ignorado, sem versão no Git. Não houve branch/fork/clone/upstream/push/deploy nem ordens de corretora.

## O que o resultado não comprova

**Executa corretamente**: API autenticada, contratos normalizados, snapshot, seis sinais reais e simulação financiada com NAV recomputado. **Resultado histórico confiável**: não demonstrado. O serviço retém revisões atuais e não um arquivo PIT completo. Perfil/setor atual e seleção de universo podem gerar vieses. Preços chegam pelo OpenMarkets, mas o vendor declarado é yfinance; não existe fallback direto ao Yahoo.

O ajuste numérico de PETR4 é compatível com proventos brutos, porém a API tem ambiguidade entre ex_date e último dia com direitos e não fornece datas de pagamento completas. Não se certificaram toda a política de splits/dividendos/JCP, retenções, fluxo de caixa e reinvestimento. O simulador não adiciona proventos novamente e não modela custos, slippage, impostos, aluguel ou juros do caixa. A janela de dez pregões não sustenta inferências sobre Sharpe ou retorno anualizado. Os LLMs atuais, mesmo com prompts blind, não recriam o conhecimento disponível em 2024.

[De-para e gaps completos](openmarkets-migration.md), [consumidores originais](openmarkets-consumers.md), [contrato das demonstrações](openmarkets-statements.md), [evidência dos payloads](openmarkets-live-audit.md).
