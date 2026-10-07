# Inventário de contratos e consumidores de dados

Inventário da implementação original, antes da adaptação OpenMarkets. O de-para
dos endpoints OpenMarkets e a evidência de cobertura complementam este documento.

## Chamadas REST originais

Todas as chamadas financeiras passam por `hedge_fund/data/client.py`, originalmente
Financial Datasets (`https://api.financialdatasets.ai`, header `X-API-Key`).

| Chamada original | Resposta esperada | Consumidores |
|---|---|---|
| `/prices/`: ticker, intervalo, multiplier, start/end | `prices[]`: open/high/low/close floats; volume int; time ISO | `pipeline/stages.py` (marcação e execução), `data/sessions.py` (calendário observado), `backtesting/fund.py`, `backtesting/engine.py`, `event_study/engine.py`, aquecimento TUI |
| `/financial-metrics/`: ticker, filing_date_lte, period, limit | `financial_metrics[]`, períodos mais recentes primeiro | `features/snapshot.py`; aquecimento TUI |
| `/company/facts/`: ticker | `company_facts` objeto | snapshot: sector/industry; aquecimento TUI; método get_market_cap |
| `/earnings/`: ticker, limit=1 | `earnings[]`, quarterly/annual aninhados | método público/cache; nenhum consumidor atual de produção |
| `/earnings/`: ticker, limit=N | `earnings[]`, um registro por divulgação | `signals/pead.py` e `event_study/engine.py` |
| `/news/`: ticker, end_date, start_date opcional, limit | `news[]` | método público/cache; nenhum consumidor atual de produção |
| `/insider-trades/`: ticker, filing_date_lte/gte, limit | `insider_trades[]` | método público/cache; nenhum consumidor atual de produção |
| get_market_cap: facts, depois financial metrics | scalar float ou None | método público/cache; nenhum consumidor atual de produção |

O modelo `Filing` é exportado, mas não existe chamada `/filings` no cliente.
O paginador original seguia `next_page_url` absoluta até o final. Isso é uma
característica de Financial Datasets, não um contrato presumido do OpenMarkets.

## Campos financeiros

`FinancialMetrics` exige ticker, report_period e period. Currency, datas de
divulgação e os demais campos aceitam None; ausência não significa zero.

| Grupo | Campos do contrato original |
|---|---|
| Publicação | currency, filing_date, filing_datetime |
| Avaliação | market_cap, enterprise_value, price_to_earnings_ratio, price_to_book_ratio, price_to_sales_ratio, enterprise_value_to_ebitda_ratio, enterprise_value_to_revenue_ratio, free_cash_flow_yield, peg_ratio |
| Rentabilidade | gross_margin, operating_margin, net_margin, return_on_equity, return_on_assets, return_on_invested_capital |
| Eficiência | asset_turnover, inventory_turnover, receivables_turnover, days_sales_outstanding, operating_cycle, working_capital_turnover |
| Liquidez | current_ratio, quick_ratio, cash_ratio, operating_cash_flow_ratio |
| Alavancagem | debt_to_equity, debt_to_assets, interest_coverage |
| Crescimento | revenue_growth, earnings_growth, book_value_growth, earnings_per_share_growth, free_cash_flow_growth, operating_income_growth, ebitda_growth |
| Por ação | payout_ratio, earnings_per_share, book_value_per_share, free_cash_flow_per_share |

O snapshot dos cinco agentes LLM utiliza market_cap, P/E, ROE, margens
bruta/operacional/líquida, dívida/patrimônio, liquidez corrente, crescimento de
receita, EPS, BVPS e FCF/ação; usa sector/industry dos facts mais recentes. Exige
pelo menos quatro períodos e normalmente pede vinte. Calcula ROE e margem média,
variação da margem bruta, CAGR de BVPS e EPS YoY.

**Contrato de periodicidade:** snapshots representam TTM em cadência trimestral,
mais recente primeiro. EPS YoY procura o mesmo trimestre do ano anterior;
quando esse trimestre está ausente, retorna None em vez de usar outra posição.
CAGR usa o intervalo real entre os report_period dos extremos com valores
disponíveis, sem encurtar anos quando há lacunas. Resultados anuais não podem
ser rotulados TTM trimestral para satisfazer o mínimo.
Razões e crescimentos percentuais são frações; multiplicadores não têm unidade;
valores monetários brasileiros são BRL absolutos e valores por ação BRL/ação.

`EarningsData` inclui revenue/estimated_revenue/revenue_surprise,
earnings_per_share/estimated_earnings_per_share/eps_surprise, net_income,
gross_profit, operating_income, weighted_average_shares e diluted,
free_cash_flow, cash_and_equivalents, total_debt/assets/liabilities,
shareholders_equity, net_cash_flow_from_operations/investing/financing,
capital_expenditure, change_in_cash_and_equivalents e mudanças de receita,
lucro, resultado operacional, lucro bruto e FCF.

`EarningsRecord` exige ticker, report_period e source_type; filing_date/datetime,
filing_window, fiscal_period, currency, URL, accession number e dados
quarterly/annual são opcionais. Os consumidores de eventos precisam de data
pública real, mesmo que o modelo permita None.

`CompanyFacts` contém ticker, is_active, name, sector, industry, category,
exchange, location e metadata americana CIK/SIC/SEC. `CompanyNews` exige ticker,
title, source; date/url são opcionais. `InsiderTrade` originalmente exigia ticker, name e filing_date. Na adaptação CVM,
nome individual e publicação tornam-se opcionais; origem, agregação, instrumento,
unidade e PIT são explícitos, pois não podem ser inferidos do nome do emissor.

## Restrições dos consumidores históricos

- PEAD depende de consenso EPS, surpresa BEAT/MISS e data de anúncio. Por padrão
  aceita somente 8-K e descarta lag de 45 dias ou mais. ITR/DFP não são 8-K.
  A ausência de cobertura OpenMarkets produz erro explícito, em vez de um
  backtest inteiramente neutro que esconda a ausência de dados.
- Event study também utiliza filing_date e filtro de 45 dias, benchmark e cerca
  de 250 pregões de estimação. A CLI original excluía eventos sem surpresa EPS.
  Essas regras de anúncios americanos não são automaticamente apropriadas à CVM.
- Datas de fim de período não provam disponibilidade pública. O atributo
  `point_in_time=False` do cliente acompanha o snapshot, seu hash, os prompts
  live/blind e os sinais LLM: o prompt avisa que dados podem ter reapresentações
  posteriores e o replay é exploratório. Clientes injetados antigos sem metadata
  mantêm o contrato PIT original para compatibilidade.
- Nenhuma distribuição de porte Ken French é carregada automaticamente nos
  agentes blind. Essa tabela compara empresas americanas em USD milhões e não
  é adequada a market cap em BRL. Uma tabela pode ser injetada explicitamente
  apenas quando o chamador verificou sua compatibilidade.
- O grid do fundo deriva dos preços observados do benchmark, não de um calendário
  de segunda a sexta; benchmark brasileiro com cobertura é essencial.
- O consumidor utiliza `time[:10]` como data do pregão e pede datas inclusivas.
  A normalização precisa conservar a data brasileira, evitando deslocamento UTC.
- Preços ajustados não equivalem a preços executáveis. Brokers mantêm ações
  inteiras e P&L de fechamento; sem lançamentos de splits/proventos, séries
  ajustadas são uma aproximação de pesquisa que requer aviso explícito.

## Cache, erros e credenciais

O cache original tinha hash somente de método/parâmetros, sem provider, schema
ou política de ajuste. Uma migração deve separar namespaces e identificar a
origem para impedir uso involuntário de respostas Financial Datasets. Latest
facts/earnings, métricas e respostas vazias originalmente não tinham TTL.

O contrato distingue ausência de dados de falha: auth, rede, rate limit e erros
de infraestrutura devem propagar; não podem virar vazio ou ser cacheados como
ausência. Falhas na paginação também não justificam retornar série parcial.

`tui/keys.apply_credentials()` carrega `.env` do cwd, depois
`~/.hedge-fund/.env`, ambos `override=False`: variáveis exportadas ganham, e o
`.env` do projeto tem precedência sobre o arquivo de usuário. As entradas CLI
chamam esse método. `.env` é ignorado pelo Git. A chave não deve aparecer em
fixtures, logs, arquivos de resultado, URLs ou relatórios de cobertura.

APIs LLM (`hedge_fund/llm/client.py`) são independentes da fonte financeira.
Migrar o mercado não requer trocar provedor/modelo LLM.
