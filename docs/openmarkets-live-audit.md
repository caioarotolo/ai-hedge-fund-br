# Auditoria dos payloads reais OpenMarkets — 2026-10-07

Esta nota resume os artefatos redigidos já capturados em `outputs/openmarkets-live/`. A auditoria leu esses arquivos e não fez novas chamadas à API. A resposta de catálogo e o histórico financeiro são páginas truncadas; portanto, os números abaixo descrevem as páginas capturadas, não o universo completo.

## Catálogo e unidades

`00-v1-metrics.json` contém 100 definições e `meta.truncated=true`, com cursor para a página seguinte. O catálogo declara `coverage_scope=definition_only`: documenta fórmulas e unidades sem comprovar cobertura da PETR4. `output_unit` é uma categoria semântica (`currency`, `ratio`, `currency_per_share`), não a unidade física do valor. Para dinheiro, a unidade física vem de `unit` (`BRL` ou `BRL/share`).

Na página real de `/financial-metrics`, `04-v1-companies-PETR4-financial-metrics.json`, as linhas trazem `metric_key`, `period_end`, `source_delivery_date`, `version`, `currency`, `unit`, `output_unit`, `value_scale`, `currency_scale`, `availability` e `value`. Os registros de moeda usam `currency=REAL`; os valores monetários observados usam `unit=BRL`, e os valores por ação usam `unit=BRL/share`. Em parte das linhas, `value_scale=normalized_from_thousands` e `currency_scale=MIL`: o valor já está normalizado para BRL e não deve ser multiplicado novamente.

A página tem 1.000 linhas, 95 `metric_key` distintos, cursor para continuação e 100% das linhas com `source_delivery_date`. Na página observada, as datas de entrega são posteriores ao fim do período. Há versões 1 e 2, sem linhas duplicadas por `metric_key` e `period_end` nessa página. A própria resposta informa que o histórico de revisões retido não é um arquivo point-in-time completo; por isso `point_in_time` continua `False`.

A API identifica `period_end` como data do período contábil e `source_delivery_date` como data de entrega/publicação disponível no serviço. O adaptador usa a segunda para cortar observações posteriores ao `end_date` e para preencher `filing_date` conservadoramente. Esse campo não substitui a data e hora original de publicação do documento CVM; `filing_datetime` permanece vazio. Para um registro agregado, `filing_date` é a data de entrega mais recente entre os valores incluídos.

O payload solicitado com `period=ltm` contém `period_kind=quarterly`. As chaves `ltm:*` identificam as medidas LTM; não se deve descartar essas linhas só porque `period_kind` é trimestral. `eps` e `fcf_per_share` observados em períodos com `period_start`/`period_end` trimestrais são valores do trimestre, não um EPS ou FCF por ação TTM. O adaptador os mapeia apenas para chamadas `quarterly` ou `annual`; em TTM, permanecem nulos.

## De-para usado pelo normalizador

O OpenAPI capturado confirma que `metric_keys` é uma string separada por vírgulas com nomes canônicos ou aliases documentados. A API rejeitou a lista de chaves com HTTP 422: o catálogo completo não reconhece price_to_revenue, peg_ratio e quick_ratio como filtros, embora essas chaves apareçam no payload financeiro. O cliente consulta history com market_date_mode=statement_date, pagina o histórico sem metric_keys e filtra localmente os campos que consegue normalizar. Isso aumenta o volume de paginação e mantém a mesma fonte OpenMarkets.

| `metric_key` OpenMarkets | Campo normalizado | Escopo |
|---|---|---|
| `market_cap`, `enterprise_value` | `market_cap`, `enterprise_value` | valor monetário BRL na data do período |
| `pe_ratio`, `pb_ratio`, `price_to_revenue` | `price_to_earnings_ratio`, `price_to_book_ratio`, `price_to_sales_ratio` | múltiplos de valuation |
| `ev_to_ebitda`, `ev_to_revenue`, `fcf_yield`, `peg_ratio` | `enterprise_value_to_ebitda_ratio`, `enterprise_value_to_revenue_ratio`, `free_cash_flow_yield`, `peg_ratio` | múltiplos/yields |
| `ltm:roe`, `ltm:roa`, `ltm:roic` | `return_on_equity`, `return_on_assets`, `return_on_invested_capital` | retorno LTM |
| `ltm:gross_margin`, `ltm:ebit_margin`, `ltm:net_margin` | `gross_margin`, `operating_margin`, `net_margin` | margens LTM |
| `ltm:interest_coverage` | `interest_coverage` | cobertura LTM |
| `asset_turnover`, `current_ratio`, `quick_ratio`, `revenue_growth` | campos de mesmo nome | giro, liquidez e crescimento |
| `bvps` | `book_value_per_share` | saldo por ação |
| `eps`, `fcf_per_share` | `earnings_per_share`, `free_cash_flow_per_share` | somente quarterly/annual |

O mapeamento deliberadamente não trata `net_debt_to_equity` como dívida total sobre patrimônio, `inventory_to_revenue` como giro de estoque, nem `pe_ratio_attributable`/`pe_ratio_total` como aliases intercambiáveis de `pe_ratio`. Métricas sem chave observada e sem semântica equivalente continuam nulas.

## Cobertura, preços e uso em backtests

A resposta de cobertura PETR4 (`01-v1-companies-PETR4-coverage.json`) lista sete famílias disponíveis, mas também mostra lacunas por métrica e períodos sem entradas aplicáveis. `meta.date_semantics` diz que as datas representam período publicado ou data do evento, não hora de ingestão. A cobertura é evidência de disponibilidade do dataset; não valida valor, revisão histórica ou semântica de cada campo.

As cotações capturadas em `03-v1-quotes-PETR4.json` e `09-v1-quotes-BOVA11.json` usam nomes como `trade_date`, `open_price`, `close_price`, `adjusted_close_price` e `tick_volume`. A metadata declara `price_basis=adjusted`, `ohlc_price_basis=raw` e `adjusted_price_field=adjusted_close_price`; assim, o fechamento ajustado não torna automaticamente OHLC ajustado. O campo `source` identifica `yfinance`, embora a resposta venha pelo endpoint OpenMarkets. Para o benchmark BOVA11, perfil e coverage responderam 404 enquanto `/quotes/BOVA11` respondeu com dez barras; consumidores de benchmark devem exigir a série de preços, não o perfil de empresa.

Uma execução offline do normalizador sobre as 1.000 linhas já capturadas produziu dez períodos e 21 campos não nulos, com todas as datas de entrega até 2024-06-14. Isso verifica a conversão do payload guardado sem gastar quota. A validação live posterior paginou o histórico completo, excluiu registros sem entrega conhecida e produziu snapshot com 18 períodos. O backtest e o CLI real passaram; resultados detalhados em openmarkets-test-results.md.

## Conferência numérica de proventos/ajustes

Na janela 2024-06-03–14, PETR4 apresenta duas distribuições, DIVIDENDO e JSCP,
cujo valor bruto total é R$ 1,04324226 por ação. O fator adjusted/raw muda de
0,73942634 em 11/06 para 0,76049335 em 12/06: razão 1,02849101. A razão teórica
`1/(1−1,04324226/37,66)` é aproximadamente 1,02849084. Isso é compatível com um
ajuste pelo valor bruto dos eventos, sujeito ao arredondamento; **não certifica**
a política completa de total return ou a tributação de JCP.

A API registra `ex_date` e `last_date_with_rights` ambos como 11/06, enquanto a
mudança aparece em 12/06. Esse conflito de semântica permanece explícito. As
datas de pagamento estão ausentes. Não há crédito separado dos eventos no
simulador (evita contagem dupla), nem reconstrução de retenções, reinvestimento
ou todos os splits. A auditoria numérica local está em
`outputs/openmarkets-live/price-adjustments.json`.
