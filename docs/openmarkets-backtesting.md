# Backtests brasileiros com OpenMarkets

Um backtest que executa sem erro demonstra o funcionamento do software. Não demonstra que os dados estavam disponíveis na data simulada nem que o retorno histórico é economicamente confiável. Os resultados recebem `historical_reliability: exploratory`, `currency: BRL`, `data_source` e uma lista de limitações. Não há ordens em corretora: o replay usa `SimBroker` em memória.

## Estado da validação real

A chave local foi autenticada em 2026-10-07. Preços de PETR4 e BOVA11 e fundamentos de PETR4 foram recebidos e normalizados. O relatório de testes registra o universo e os intervalos efetivamente confirmados; presets de universo não constituem promessa de cobertura de cada campo.

## Datas, execução e moeda

- O benchmark padrão passou a `BOVA11`, ETF brasileiro usado como referência; ele não é o próprio índice Ibovespa. Sua cobertura deve ser confirmada para cada intervalo de teste.
- A grade de sessões usa as datas das barras reais do benchmark. Não são criados pregões por uma lista presumida de feriados.
- O limite conservador de dados completos exclui a data corrente em `America/Sao_Paulo`, inclusive depois do fechamento.
- No replay principal (`aihf backtest`), uma decisão é executada no próximo fechamento observado. As posições são marcadas em cada sessão; ausência de uma cotação necessária provoca erro.
- O capital e os resultados são expressos em BRL; não há conversão de preços brasileiros para USD.
- `HEDGE_FUND_HOME` permite separar mandatos, cache, artefatos de pesquisa e credenciais de usuário sem modificar `~/.hedge-fund`.

## Fundamentos, preços e confiabilidade

Datas de encerramento de trimestre, ano ou DRE não provam a data de divulgação. Uma observação fundamental atual ou reapresentada não pode ser apresentada como a versão conhecida em uma sessão histórica. O adaptador usa source_delivery_date para excluir entregas posteriores à simulação, mas o provedor conserva revisões atuais, sem arquivo completo de versões originais. Por isso o uso dos fundamentos é exploratório. O adaptador sinaliza esse fato em logs e nos metadados `point_in_time=False`; o resultado do replay permanece explicitamente exploratório. Não há flag que transforme esses dados em point-in-time.

Antes de interpretar retornos, é necessário confirmar se os preços são brutos, ajustados por desdobramentos/grupamentos ou ajustados também por dividendos e JCP. O simulador não altera quantidades por eventos societários nem credita proventos separadamente. Se as barras forem brutas, eventos podem distorcer NAV e retorno. Se forem ajustadas por proventos, o replay não reproduz o fluxo de caixa real, as retenções tributárias ou a escolha de reinvestimento. Não se deve adicionar dividendos novamente a uma série que já os incorpora.

O simulador também não inclui emolumentos, corretagem, slippage, impacto de mercado, impostos, aluguel de ações ou juros sobre caixa/margem. O universo selecionado hoje pode produzir viés de sobrevivência. O benchmark pode ter tratamento de proventos diferente do das ações. Sharpe e retorno anualizado em janelas curtas exigem cautela.

O antigo harness de alpha (`BacktestEngine`) não substitui o replay principal: ele usa entrada no fechamento da previsão, uma curva de resultados por operação e Sharpe por operações, sem uma carteira diária plenamente financiada. Use `aihf backtest` para avaliar a mecânica de carteira.

## PEAD e estudos de eventos

Os consumidores PEAD/earnings precisam de consenso de EPS, classificação BEAT/MISS e data de anúncio dos resultados. Uma DRE brasileira, ITR ou DFP não substitui esse contrato. Não são fabricadas surpresas EPS, datas de anúncio ou formulários SEC para dar continuidade ao fluxo.

Os comandos `python -m hedge_fund.backtesting` e `python -m hedge_fund.event_study`, que antes demonstravam esse fluxo, agora terminam com código 2 e uma mensagem específica da falta de cobertura. Isso é uma falha de capacidade explicitada, não um backtest com zero operações considerado bem-sucedido. O mandato de exemplo brasileiro usa apenas a estratégia `deep-value`. O template separado `earnings-drift` mantém a dependência PEAD; selecioná-lo exige cobertura que ainda falta.

## Preparação reproduzível

Execute na raiz do checkout para que o `.env` local seja carregado. Variáveis já exportadas têm precedência; o `.env` local tem precedência sobre o arquivo de usuário. Não imprima, inclua em argumentos ou versione a chave.

```bash
cd /home/caio/repos/ai-hedge-fund-br
UV_CACHE_DIR=/tmp/ai-hedge-uv-cache /home/caio/.local/bin/uv venv --python /usr/bin/python3.12 .venv
UV_CACHE_DIR=/tmp/ai-hedge-uv-cache /home/caio/.local/bin/uv pip install --python .venv/bin/python -e . pytest
.venv/bin/python -m pytest hedge_fund -q
export HEDGE_FUND_HOME="$PWD/scratchpad/openmarkets/runtime"
mkdir -p scratchpad/openmarkets
```

Configure `OPENMARKETS_API_KEY` no `.env` com seu editor. Os analistas de fundamentos usam o provedor/modelo de LLM já configurado no projeto e precisam de sua credencial correspondente; a migração de dados não troca esse provedor.

Valide primeiro as respostas reais e a construção de snapshots, sem consumir LLM:

```bash
.venv/bin/python -m hedge_fund.validate_openmarkets \
  --universe PETR4,VALE3,ITUB4 --benchmark BOVA11 \
  --start 2024-06-03 --end 2024-06-14 \
  --out outputs/openmarkets-validation
```

Sem `--mandate`, a rotina verifica a cobertura de dados e os snapshots; ela não executa uma análise LLM nem um backtest de estratégia. Uma chave ausente ou um gap necessário produz código de saída 2 e os artefatos identificam o bloqueio. Examine os relatórios em `outputs/openmarkets-validation` antes de escolher o universo e as datas do replay.

Para incluir os analistas reais e o replay do mandato brasileiro, use o mesmo comando com `--mandate`:

```bash
.venv/bin/python -m hedge_fund.validate_openmarkets \
  --universe PETR4,VALE3,ITUB4 --benchmark BOVA11 \
  --start 2024-06-03 --end 2024-06-14 \
  --out outputs/openmarkets-validation \
  --mandate hedge_fund/fund/example.yaml --model gpt-4.1-mini
```

Se necessário, acrescente `--model` com o identificador do modelo já utilizado e configurado no projeto. Essa execução depende da credencial LLM correspondente. Os relatórios devem distinguir falta de acesso a dados, abstenção de analistas e uma simulação efetivamente executada.

```bash
cat > scratchpad/openmarkets/fundamentals-br.yaml <<'YAML'
schema_version: 2
name: fundamentals-br
strategies:
  - name: value
    blend: {mode: long_only, gross_target: 1.0}
    models:
      - name: graham
risk:
  max_position_pct: 0.25
  max_gross_exposure: 1.0
capital: 100000
rebalance: monthly
benchmark: BOVA11
YAML
```

Após confirmar a cobertura e aceitar as limitações exploratórias acima, execute:

```bash
.venv/bin/python -m hedge_fund.run --model gpt-4.1-mini backtest \
  scratchpad/openmarkets/fundamentals-br.yaml \
  --universe PETR4,VALE3,ITUB4 \
  --start 2025-01-01 --end 2025-03-31 \
  --out scratchpad/openmarkets/backtest-2025q1.json \
  > scratchpad/openmarkets/backtest-stdout.json
```

Esse exemplo de janela 2025Q1 deve ser validado separadamente; os resultados efetivamente executados estão no relatório de testes. O resultado JSON deve identificar `data_source: openmarkets`. Inspecione `records` e as decisões para identificar abstenções: um resultado sem ordens ou com todos os analistas abstendo não demonstra que análise e cálculos sobre fundamentos funcionaram. Preserve logs e contagens de chamadas reais, além do retorno e do número de ordens.

Os arquivos em `scratchpad/` são artefatos locais ignorados pelo Git. Não faça push, deploy, tick de fundo em corretora ou envio de ordens para reproduzir esses backtests.

## Modelo OpenAI testado

Após a chave OpenAI ser salva, `gpt-4.1-mini` foi confirmado no catálogo autenticado e respondeu pelo cliente do projeto. Para usar essa credencial no CLI, o argumento global precede `backtest`:

```bash
.venv/bin/python -m hedge_fund.run --model gpt-4.1-mini backtest \
  hedge_fund/fund/example.yaml --universe PETR4,VALE3,ITUB4 \
  --start 2024-06-03 --end 2024-06-14
```

No validador, `--model gpt-4.1-mini` pode ser acrescentado ao comando com `--mandate`. A chave OpenAI atende os analistas; `OPENMARKETS_API_KEY` continua sendo necessária para os dados. A confirmação do LLM não confirma a cobertura de mercado.


## Execuções efetivamente confirmadas

O relatório final registra 697 testes locais aprovados, 12 opt-in live aprovados,
backtest de PETR4 e CLI com PETR4/VALE3/ITUB4 em 03–14/06/2024. O CLI gerou 18
sinais, zero abstenções e duas ordens simuladas. Retorno −1,6378%, benchmark
−1,8744%; são resultados exploratórios, sem certificação PIT/total return.

Para conferir também os views contábeis e movimentos/proventos:

```bash
export HEDGE_FUND_HOME="$PWD/scratchpad/openmarkets/runtime"
.venv/bin/python -m hedge_fund.validate_openmarkets_extended \
  --ticker PETR4 --start 2024-01-01 --end 2024-06-14 \
  --out outputs/openmarkets-extended
```

O reteste desse comando passou em todos os quatro checks: demonstrativos latest,
histórico de 12 períodos, 60 movimentos datados de insiders e seis proventos.
Ele não fornece o consenso e anúncios necessários ao PEAD. O modelo LLM atual,
mesmo em modo blind, não recria o conhecimento disponível na data histórica.
