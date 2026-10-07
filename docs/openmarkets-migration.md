# Migração REST OpenMarkets

A autenticação real foi confirmada em 2026-10-07 após corrigir o nome da variável para `OPENMARKETS_API_KEY`. Preços, perfil, cobertura, métricas, demonstrações, proventos, insiders, datasets e OpenAPI foram comparados com respostas autenticadas. Os resultados de execução estão em [openmarkets-test-results.md](openmarkets-test-results.md). O núcleo de fundamentos/preços está migrado; **a migração de todos os fluxos originais não é completa**: notícias REST e anúncios/consenso EPS necessários ao PEAD não existem no contrato confirmado.

Referências: [documentação REST](https://openmarkets.com.br/docs/api-rest) e OpenAPI autenticado `GET https://api.openmarkets.com.br/v1/openapi.json`. O caminho `/openapi.json` sem `/v1` retorna 404. O inventário de todas as chamadas e consumidores originais está em [openmarkets-consumers.md](openmarkets-consumers.md). Contratos reais e exemplos numéricos: [openmarkets-live-audit.md](openmarkets-live-audit.md).

## De-para completo dos endpoints usados

| Original → método | OpenMarkets | Transformação confirmada | Lacuna / impacto |
|---|---|---|---|
| `/prices/` → `get_prices` | `/v1/quotes/{ticker}` | `symbol`, `trade_date`, `*_price`, `tick_volume`, `currency`; solicita diário histórico ajustado. `adjusted_close_price` vira close; OHLC bruto recebe fator adjusted/raw close. Ordena, recorta, deduplica; BRL, fonte HTTP OpenMarkets e vendor original registrados | A API informa vendor `yfinance` e `provider_finality_verified=false`. Ajustado é proxy de pesquisa, não preço executável; fórmula/JCP/impostos e finalização não certificados |
| `/financial-metrics/` → `get_financial_metrics` | `/v1/companies/{ticker}/financial-metrics`; catálogo `/v1/metrics` | `ttm→ltm`, `mode=history`, `frequency=period`, consolidado; pagina histórico sem filtro de chaves incompatível, seleciona localmente as chaves abaixo, pivota período, resolve versões/aliases, filtra `source_delivery_date<=cutoff`, limita após paginação | Retém revisões atuais, não arquivo PIT completo; registros sem entrega conhecida são excluídos. EPS/FCF por ação de trimestre não são TTM e permanecem nulos em TTM |
| `/company/facts/` → `get_company_facts` | `/v1/companies/{ticker}` | `company_name`, `sector`, `segment`, `b3_status`; B3/Brasil | Perfil atual, sem setor histórico ou metadata SEC; BOVA11 tem quotes mas perfil/coverage de companhia retornam 404 |
| facts/metrics → `get_market_cap` | Métricas acima | Campo histórico `market_cap` em BRL; não utiliza valor atual de perfil | Valor ausente continua nulo |
| `/insider-trades/` → `get_insider_trades` | `/v1/governance/insiders`, `view=movements`, `period=all` | Reconstrói data usando mês de `referencia_data` e `dia`; API filtra mês, portanto início é primeiro dia e corte final é local. Quantidade/preço/valor/balances; shares somente `local_share`; ignora somente resumos explícitos sem_mov sem transação e exclui movimentos após o corte | Agregados CVM não identificam pessoa nem publicação: `name/filing_date=None`, `is_aggregated`, `point_in_time=False`. Nome do emissor não vira nome do insider; ticker_scope=reporting_company, sem garantir classe/ticker do instrumento; acesso conforme plano |
| `/earnings/` latest → `get_earnings` | `/v1/companies/{ticker}/financial-statements`, quarterly + annual | Filtra canonical_names, normaliza MIL ×1.000, ITR/DFP e consolidação. Trimestre isolado por diferença de YTD compatível; Q4 = anual−Q3 quando possível; balanço não é subtraído | Fatos contábeis, sem consenso, surprise, anúncio, médias de ações inventadas. [Mapa de contas](openmarkets-statements.md) |
| `/earnings/` history → `get_earnings_history` | Demonstrações acima | Registros de períodos ITR/DFP, BRL, dados publicados/reapresentados, datas de anúncio nulas | **PEAD e event study bloqueados** por consenso EPS e anúncio original; ITR/DFP não equivalem a 8-K |
| `/news/` → `get_news` | Sem rota no catálogo REST de 22 endpoints | `CoverageError` explícito | Nenhum artigo fictício nem fallback; método público sem consumidor atual de produção |
| Ken French ZIP | Sem equivalente | Download automático removido dos agentes blind | Distribuição americana em USD não representa ações brasileiras; não inventa ranking |

`FDClient` e `FDClientError` são aliases de compatibilidade da implementação OpenMarkets. Não existe chamada ao provedor antigo nem fallback direto a Yahoo. As APIs de LLM foram preservadas; `gpt-4.1-mini` foi selecionado explicitamente para testar a chave OpenAI existente, mantendo o padrão original.

## Campos e unidades financeiros

| Chave OpenMarkets | Campo normalizado |
|---|---|
| market_cap, enterprise_value | mesmos nomes |
| pe_ratio, pb_ratio, price_to_revenue | price_to_earnings_ratio, price_to_book_ratio, price_to_sales_ratio |
| ev_to_ebitda, ev_to_revenue | enterprise_value_to_ebitda_ratio, enterprise_value_to_revenue_ratio |
| fcf_yield, peg_ratio | free_cash_flow_yield, peg_ratio |
| ltm:roe, ltm:roa, ltm:roic | return_on_equity, return_on_assets, return_on_invested_capital |
| ltm:gross_margin, ltm:ebit_margin, ltm:net_margin | gross_margin, operating_margin (EBIT/revenue), net_margin |
| ltm:interest_coverage | interest_coverage |
| asset_turnover, current_ratio, quick_ratio, revenue_growth | mesmos nomes |
| bvps | book_value_per_share |
| eps, fcf_per_share | earnings_per_share, free_cash_flow_per_share em annual ou quarterly isolado comprovado |

Para quarterly/annual, as chaves de rentabilidade são normalizadas sem prefixo LTM. Em history, market_date_mode=statement_date é explícito. O catálogo completo não aceita três chaves presentes nos próprios dados (price_to_revenue, peg_ratio e quick_ratio) como filtros; por isso o cliente não envia metric_keys e filtra localmente após paginar o histórico. Todos os outros campos do modelo permanecem nulos se não houver equivalente confirmado. Não substitui `debt_to_equity` por `net_debt_to_equity`, nem valor por ação trimestral por TTM. Em quarterly, EPS/FCF por ação exigem period_start no começo do trimestre; YTD permanece nulo e não é subtraído, porque o denominador pode mudar.

Métricas reais têm `unit=BRL`, `value_scale=normalized_from_thousands`, `currency_scale=MIL`: **já estão em reais**, sem segunda multiplicação. Demonstrativos em MIL continuam em milhares e recebem ×1.000. `REAL→BRL`. Percentuais só são divididos por 100 quando a unidade diz percent; ratio permanece fração. `output_unit=currency` é uma categoria, não escala. Valor não finito, unidade desconhecida ou conflito de versão/valor causa erro.

`source_delivery_date` informa uma entrega efetiva de uma versão retida; a data máxima dos campos utilizados vira `filing_date`. Não é fabricada a partir do fim do trimestre. Mesmo com esse corte, versões antigas podem faltar: `point_in_time=False`. Cache de prefetch também exclui períodos entregues após cada cutoff, podendo omitir uma versão anterior que já não está disponível. CAGR usa intervalo real de datas; EPS YoY exige o mesmo trimestre anterior e fica nulo sem EPS TTM adequado.

## Catálogo de 22 rotas inspecionado

O OpenAPI e `/v1/datasets` autenticados confirmam: companies/search; companies/{ticker}; datasets; datasets/{id}; companies/{ticker}/coverage; metrics; quotes/{ticker}; companies/{ticker}/financial-statements; financial-metrics; kpis; kpis/history; proventos; macro/series/{id}; macro/curves/{id}; market/series; market/series/{code}; governance/entities; governance/insiders; governance/documents; governance/documents/{document_id}; governance/documents/{document_id}/chunks; documents/search (todos com prefixo `/v1`). Os endpoints sem consumidores originais foram examinados para possíveis equivalentes; não foram incorporados artificialmente. Existência de documentos não fornece consenso EPS ou prova de arquivo PIT completo.

## Paginação, cache e credenciais

Cursor opaco na mesma rota/host, preservando filtros; erro de página/cursor repetido/redirect não produz histórico parcial. Espaçamento conservador 6,1 s, retries 429 limitados e Retry-After; quota não provoca troca de fonte. Cache tem namespace OpenMarkets/schema/base ajustada, TTL de 24 h, escrita atômica e não guarda erros. Prefetch reduz chamadas por pregão e respeita intervalos concluídos e datas de entrega conhecidas.

Tickers brasileiros normalizados, inclusive `.SA`; preços/sessões nas datas de São Paulo; benchmark BOVA11 e calendário de barras observadas. Dia corrente é excluído conservadoramente. BRL e R$ em carteira e apresentação.

Carregamento: shell → `.env` do diretório atual → arquivo de usuário, sem override. A chave vai no header Bearer, nunca em URL/logs/resultados. `.env` continua ignorado e preservado. `HEDGE_FUND_HOME` isola runtime em `scratchpad/`; resultados locais em `outputs/` também são ignorados.

Os [comandos exatos e limites de confiabilidade](openmarkets-backtesting.md) distinguem execução correta de histórico confiável. Backtest exploratório não certifica retorno realizável, ajustes/JCP ou disponibilidade PIT.
