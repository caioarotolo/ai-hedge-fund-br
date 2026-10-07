# Validação OpenAI — 2026-10-07

A auditoria foi conduzida pelo agente Codex GPT-6 Luna em esforço máximo. A chamada de integração da aplicação usou o modelo da API OpenAI `gpt-4.1-mini`; esses são modelos distintos.

O catálogo OpenAI respondeu HTTP 200 e a completion anterior passou em 3,23 s. Naquele momento, o relatório registrava que `gpt-4.1-mini` ainda não constava no registry. O modelo foi então incluído em `hedge_fund/llm/api_models.json`; a conferência posterior confirmou a correspondência entre o registry e o provedor OpenAI.

A revisão encontrou que `make_llm()` aceitava `max_tokens`, mas o ramo OpenAI não encaminhava esse limite para `ChatOpenAI`. O construtor agora recebe o valor configurado. A regressão testa `gpt-4.1-mini` com limite 32, e uma nova completion real com `max_tokens=32` e timeout de 30 s retornou o JSON de status esperado em 2,32 s. A resposta bruta e a credencial não foram gravadas no relatório.

A suite completa concluiu com **670 testes aprovados e 12 ignorados** em 27,41 s. Os ignorados são as validações live opcionais OpenMarkets.

Naquela etapa ainda não havia credencial OpenMarkets carregada. Posteriormente o nome da variável foi corrigido e a autenticação de mercado funcionou; a validação atual está em openmarkets-test-results.md. A completion de conexão descrita aqui, isoladamente, valida apenas o caminho do provedor de linguagem.
