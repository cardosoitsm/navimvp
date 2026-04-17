# Navi MVP — Especificação do Produto

> Documento gerado a partir da análise completa do código-fonte.
> Última atualização: 2026-04-16

---

## Visão Geral

O **Navi** é um assistente financeiro pessoal via **WhatsApp**. O usuário não instala nenhum aplicativo — toda a interação acontece por mensagens de texto, imagens e PDFs enviados diretamente no WhatsApp.

### Proposta de valor
- Registrar gastos e receitas em linguagem natural
- Controlar orçamentos mensais por categoria com alertas automáticos
- Receber e interpretar extratos bancários e faturas de cartão de crédito
- Responder perguntas sobre saúde financeira, limites e histórico de gastos
- Guiar o usuário por um onboarding conversacional completo antes de entrar no uso pleno

### Canais
| Canal | Uso |
|---|---|
| WhatsApp (via Twilio) | Interface principal de todos os usuários |
| API REST (JWT) | Interface alternativa para testes e integrações |

---

## Stack Tecnológica

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| Web Framework | FastAPI + Uvicorn |
| Banco de dados | PostgreSQL 15 |
| Acesso ao banco | psycopg2-binary (sem ORM, SQL direto) |
| IA / NLP | OpenAI API — `gpt-4.1-mini` |
| Leitura de PDF | pypdf |
| Mensageria WhatsApp | Twilio |
| Autenticação | JWT (python-jose) + bcrypt (passlib) |
| Configuração | pydantic-settings (.env) |
| Deploy | Docker Compose |

---

## Modelo de Dados

### Tabelas

| Tabela | Descrição |
|---|---|
| `usuarios` | Contas de usuário. WhatsApp users usam o número como `email` e senha gerada automaticamente. |
| `transacoes` | Despesas e receitas registradas. Colunas: `tipo`, `categoria`, `valor`, `user_id`, `created_at`. |
| `confirmacoes_pendentes` | Transação aguardando confirmação explícita do usuário (uma por vez, por usuário). |
| `configuracoes_usuario` | Estado do onboarding e flags de progresso: `onboarding_state`, `pending_card_total`, `pending_card_index`, `orcamento_onboarding_concluido`, `documentos_onboarding_concluido`, `aguardando_documento`, `custos_onboarding_concluido`, `ultimo_topico`, `ultimo_cartao_id`. |
| `orcamentos` | Limites mensais por categoria. Chave primária composta `(user_id, categoria)`. |
| `orcamento_alertas` | Registro de alertas já disparados. Garante que cada nível (50%, 80%, 100%) é enviado uma única vez por mês por categoria. |
| `documentos_financeiros` | Documentos recebidos (extrato, fatura, PDF genérico). Armazena URL da mídia, tipo, status de processamento e JSON extraído pela IA. |
| `perfil_financeiro` | Snapshot financeiro do usuário: saldo estimado, renda identificada, despesas fixas estimadas, pressão de cartão. |
| `faturas_cartao` | Faturas extraídas de documentos: valor total, vencimento, pagamento mínimo, emissor, cartão vinculado. |
| `cartoes_usuario` | Cartões cadastrados: nome, ordem, melhor dia de compra, limite de crédito, flag `ativo`. |
| `custos_mensais` | Custos fixos e variáveis inferidos do extrato: descrição, categoria, valor médio, tipo (`fixo`/`variavel`), flag `confirmado`. |

### Migrations
Todas as DDLs são executadas via `SCHEMA_STATEMENTS` em `app/db.py` no startup da aplicação. Não há ferramenta de migration separada — os statements usam `CREATE TABLE IF NOT EXISTS` e `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` para serem idempotentes.

---

## Endpoints da API

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| GET | `/` | — | HTML de boas-vindas |
| GET | `/health` | — | Status do app e do banco de dados |
| POST | `/webhook` | — | Entrada principal do WhatsApp (Twilio) |
| GET+POST | `/webhook-fallback` | — | Fallback Twilio para instabilidades |
| POST | `/register` | — | Cria usuário com email e senha |
| POST | `/login` | — | Retorna JWT |
| POST | `/chat` | Bearer JWT | Chat direto via API |
| POST | `/admin/reset-user` | X-Admin-Key | Deleta usuário e todos os seus dados |

---

## Fluxo de Onboarding

O onboarding é uma máquina de estados persistida em `configuracoes_usuario.onboarding_state`. Cada novo usuário do WhatsApp percorre os estados abaixo em ordem, podendo pular qualquer etapa respondendo "PULAR" ou "não".

```
[novo usuário]
      │
      ▼
ACCOUNT_SNAPSHOT_PENDING
  Pede saldo atual ou extrato bancário.
  Aceita: valor numérico, mídia (extrato), "pular".
      │
      ▼
BUDGET_SETUP_PENDING
  Pede limites mensais por categoria.
  Aceita: "farmacia 290, mercado 1200, lazer 500", "pular".
      │
      ▼
COST_REVIEW_PENDING  (somente se extrato foi enviado com lançamentos identificáveis)
  Apresenta custos fixos e variáveis inferidos do extrato para confirmação.
  Aceita: "sim", "não", ajustes como "seguro é fixo", "pular".
      │
      ▼
CARD_COUNT_PENDING
  Pergunta quantos cartões o usuário quer cadastrar.
  Aceita: dígitos (1, 2, 3...), palavras (um, dois...), "pular", "não tenho cartão".
      │
      ▼
CARD_NAMES_PENDING
  Pede os nomes dos cartões cadastrados.
  Aceita: lista separada por vírgula; parsing via GPT + fallback por regex.
      │
      ▼
CARD_DETAILS_PENDING
  Para cada cartão: pede melhor dia de compra e limite de crédito.
  Aceita: "melhor dia 20, limite 5000"; parsing por regex.
      │
      ▼
CARD_INVOICE_PENDING
  Para cada cartão: pede a fatura atual (imagem ou PDF).
  Aceita: mídia; "pular" avança para o próximo cartão.
      │
      ▼
DOCUMENT_ONBOARDING_PENDING
  Convida para enviar extrato ou fatura caso ainda não tenha sido enviado.
  Aceita: mídia, "sim", "pular".
      │
      ▼
ONBOARDING_COMPLETE
  Fluxo normal de uso.
```

### Regras de transição importantes
- Se um extrato foi enviado durante o onboarding, `CARD_COUNT_PENDING` pode ir direto para `ONBOARDING_COMPLETE` (sem precisar de documentos).
- Se `card_total <= 0`, pula direto para `DOCUMENT_ONBOARDING_PENDING` ou `ONBOARDING_COMPLETE`.
- Após `ONBOARDING_COMPLETE`, o usuário pode re-acionar o cadastro de cartões a qualquer momento com frases como "quero cadastrar meus cartões".

---

## Funcionalidades por Sprint

---

### S1 — Base do produto

**Objetivo:** estrutura mínima para registrar transações via WhatsApp e consultar gastos.

#### [S1] Registro de transações via texto
- O usuário envia mensagens como "gastei 50 no Uber" ou "paguei 120 no mercado".
- O Navi usa GPT-4.1-mini para extrair `tipo`, `categoria` e `valor`.
- Fallback por regras (keywords) antes de chamar a IA.
- Se a mensagem for ambígua (sem verbo claro + valor explícito), solicita confirmação antes de salvar.
- **Critérios de aceite:**
  - [ ] Transação com verbo + valor é salva diretamente.
  - [ ] Transação ambígua gera pergunta de confirmação ("Acho que entendi assim... Confirma?").
  - [ ] "SIM" salva a transação; "NÃO" descarta e pede correção.

#### [S1] Consulta de gastos do mês
- O usuário pergunta "quanto gastei?" ou "quanto gastei em transporte?".
- Retorna gastos agrupados por categoria + total, ou total de uma categoria específica.
- **Critérios de aceite:**
  - [ ] "quanto gastei?" retorna lista por categoria e total geral.
  - [ ] "quanto gastei em [categoria]" retorna total daquela categoria.
  - [ ] Retorna mensagem amigável se não houver registros.

#### [S1] Listagem de últimas transações
- O usuário pede "meus últimos gastos" ou "últimas transações".
- Retorna as 5 transações mais recentes com tipo, categoria e valor.

---

### S2A — Orçamentos e alertas

#### [S2A] Cadastro de budgets por categoria — Issue #5
- O usuário define limites mensais por categoria durante o onboarding ou a qualquer momento.
- Formato aceito: `"farmacia 290, mercado 1200, lazer 1000"`.
- Categorias suportadas: `farmacia`, `mercado`, `alimentacao`, `lazer`, `transporte`, `moradia`, `saude`.
- Persistido em `orcamentos` com upsert (atualiza se já existir).
- **Critérios de aceite:**
  - [ ] Limite salvo corretamente para cada categoria informada.
  - [ ] Atualização de limite existente funciona.
  - [ ] Confirmação visual dos limites cadastrados após salvar.

#### [S2A] Alertas de consumo do limite — Issue #6
- Após cada transação registrada, o Navi verifica o consumo da categoria e dispara alertas em 3 níveis:
  - 50% do limite consumido
  - 80% do limite consumido
  - 100% do limite consumido (estourado)
- Cada alerta é disparado **uma única vez por mês por nível** (idempotente via `orcamento_alertas`).
- **Critérios de aceite:**
  - [ ] Alerta de 50% enviado na primeira transação que ultrapassa esse limiar.
  - [ ] Alerta não é reenviado se já foi enviado no mesmo mês.
  - [ ] Alerta de 80% e 100% seguem a mesma lógica.

#### [S2A] Perguntas sobre orçamento via chat — Issue #7
- O usuário pergunta "quanto ainda posso gastar com farmácia?".
- Retorna: limite configurado, valor gasto, percentual e saldo restante.
- **Critérios de aceite:**
  - [ ] Resposta correta quando há limite configurado.
  - [ ] Mensagem amigável quando não há limite para a categoria.

---

### S2B — Documentos financeiros

#### [S2B] Armazenamento e análise inicial de documentos — Issue #10
- O usuário pode enviar imagens (JPEG, PNG, WebP) ou PDFs de extratos e faturas.
- O Navi identifica o tipo do documento pelo contexto da mensagem e tipo MIME.
- O documento é salvo imediatamente em `documentos_financeiros` com status `recebido`.
- O processamento pela IA roda em **background task** (não bloqueia a resposta ao usuário).
- Para PDFs: texto extraído com pypdf antes de enviar ao GPT.
- Para imagens: enviadas como base64 para o GPT (visão multimodal).
- O JSON extraído inclui: tipo do documento, saldo, renda, fatura, vencimento, pagamento mínimo, lançamentos de crédito e débito, despesas fixas estimadas.
- **Critérios de aceite:**
  - [ ] Tipos suportados: `image/jpeg`, `image/png`, `image/webp`, `application/pdf`.
  - [ ] Tipos não suportados retornam mensagem explicativa sem salvar.
  - [ ] Documento salvo imediatamente com status `recebido`; confirmação enviada ao usuário.
  - [ ] Processamento ocorre em background sem travar a conversa.
  - [ ] Após processamento, `extracted_json` e status `processado` são atualizados.

#### [S2B] Uso inicial dos dados extraídos de documentos — Issue #11
- Dados extraídos dos documentos alimentam `perfil_financeiro`: saldo estimado, renda identificada, despesas fixas estimadas, pressão de cartão.
- Faturas extraídas são salvas em `faturas_cartao` vinculadas ao `cartao_id` quando disponível.
- Renda é validada: descartada se for investimento, transferência, estorno ou sem evidência explícita de salário.
- Pressão de cartão calculada pela razão `fatura / renda` (alta ≥ 50%, moderada ≥ 25%).
- **Critérios de aceite:**
  - [ ] Saldo, renda e despesas fixas do extrato salvos em `perfil_financeiro`.
  - [ ] Fatura de cartão salva em `faturas_cartao` com valor, vencimento e pagamento mínimo.
  - [ ] Renda de origem duvidosa (resgate, PIX avulso) descartada.

---

### S2C — Cartões e configuração avançada

#### [S2C] Cadastro de saldo ou extrato inicial — Issue #12
- Primeira etapa do onboarding: o Navi pede o saldo atual da conta ou o extrato bancário.
- Aceita: valor numérico ("3200", "R$ 3.200,00"), mídia (extrato em imagem/PDF), ou "pular".
- Saldo salvo em `perfil_financeiro.saldo_atual_estimado`.
- Extrato processado em background e saldo extraído automaticamente.
- **Critérios de aceite:**
  - [ ] Valor numérico salvo corretamente.
  - [ ] Extrato recebido e salvo; onboarding avança.
  - [ ] "Pular" avança para próxima etapa sem bloquear.
  - [ ] Mensagens com contexto de cartão/fatura/limite não são interpretadas como saldo.

#### [S2C] Leitura inicial de custos — Issue #13
- Após o onboarding de budgets, se um extrato foi processado com lançamentos identificáveis, o Navi apresenta uma lista de custos fixos e variáveis inferidos para revisão.
- Custos fixos: aluguel, condomínio, mensalidade, seguro, telefone, internet, energia, água, parcela, financiamento, academia.
- Custos variáveis: mercado, farmácia, Uber, iFood, restaurante, boleto.
- O usuário pode: confirmar ("sim"), ajustar ("seguro é fixo", "mercado entra em alimentação") ou pular.
- Custos confirmados salvos em `custos_mensais` com `confirmado = TRUE`.
- **Critérios de aceite:**
  - [ ] Etapa exibida apenas se há lançamentos classificáveis no extrato.
  - [ ] "Sim" salva todos os custos como confirmados.
  - [ ] Ajustes de tipo (fixo/variável) e categoria são aplicados antes de salvar.
  - [ ] "Pular" avança sem salvar.
  - [ ] Máximo de 4 itens por grupo exibidos.

#### [S2C] Cadastro de cartões — Issue #14
- O Navi pergunta quantos cartões o usuário quer acompanhar.
- Aceita: dígito (1–5), palavra por extenso (um, dois, três...), "pular", "não tenho cartão".
- Em seguida, pede os nomes dos cartões (ex.: "Nubank, Itaú, Cartão da Casa").
- Nomes parseados via GPT-4.1-mini (LLM-first) com fallback por regex.
- Cartões salvos em `cartoes_usuario` com `ordem` e flag `ativo`.
- Confirmação visual dos nomes cadastrados.
- Se `card_total = 0`, avança direto para próxima etapa.
- Cadastro pode ser reativado a qualquer momento após o onboarding com frases como "quero cadastrar meus cartões".
- **Critérios de aceite:**
  - [ ] Quantidade parseada corretamente de dígitos e palavras.
  - [ ] Nomes parseados com LLM e validados (não aceita saudações ou frases como nomes).
  - [ ] Cartões salvos na ordem informada.
  - [ ] Confirmação visual dos nomes após salvar.
  - [ ] "Pular" avança sem cadastrar cartões.
  - [ ] Reativação do fluxo de cartões pós-onboarding funciona.

#### [S2C] Melhor dia de compra — Issue #15
- Para cada cartão cadastrado, o Navi pergunta o melhor dia de compra e o limite de crédito.
- Aceita: "melhor dia 20, limite 5000"; parsing por regex com suporte a "k" e "m" (5k = 5000).
- Salvo em `cartoes_usuario.dia_melhor_compra` e `cartoes_usuario.limite_credito`.
- "Pular" avança para o próximo cartão sem preencher os detalhes.
- **Critérios de aceite:**
  - [ ] Dia parseado corretamente (1–31).
  - [ ] Limite parseado com suporte a formatos "5000", "5.000", "5k".
  - [ ] Dados salvos no cartão correto.
  - [ ] "Pular" avança sem bloquear.

#### [S2C] Limite do cartão — Issue #16
- O limite de crédito é coletado junto ao melhor dia de compra (mesma etapa `CARD_DETAILS_PENDING`).
- Ver critérios de aceite da Issue #15.

#### [S2C] Vínculo de fatura por cartão — Issue #17
- No onboarding, após configurar os detalhes de cada cartão, o Navi pede a fatura atual daquele cartão.
- A fatura enviada é registrada em `documentos_financeiros` com `cartao_id` vinculado.
- Após processamento, a fatura é salva em `faturas_cartao` com `cartao_id`.
- Consultas de fatura buscam primeiro por `cartao_id`; fallback por nome do emissor.
- **Critérios de aceite:**
  - [ ] Fatura processada vinculada ao `cartao_id` correto.
  - [ ] Consulta "qual o valor da minha fatura do Nubank?" retorna dados do cartão correto.
  - [ ] Fallback por nome do emissor quando `cartao_id` não está vinculado.

---

### S3 — Conversação mais inteligente

#### [S3] Follow-up contextual — Issue #19
- O Navi salva o último tópico da conversa (`ultimo_topico`) e o último cartão referenciado (`ultimo_cartao_id`).
- Perguntas de follow-up como "e da Nubank?" após uma consulta de fatura são reconhecidas como continuação.
- **Critérios de aceite:**
  - [ ] Após consulta de fatura, pergunta "e do Bradesco?" retorna fatura do Bradesco.
  - [ ] Follow-up reconhecido quando a mensagem começa com "e do/da" ou tem até 6 palavras.

#### [S3] Reduzir comportamento de chatbot de menu — Issue #20
- O bot não deve exibir menus numerados.
- Respostas devem usar linguagem natural, não listas de opções enumeradas.
- **Critérios de aceite:**
  - [ ] Nenhuma resposta contém "1.", "2.", "3." como opções de menu.
  - [ ] Orientações de próximos passos são dadas em linguagem natural.

#### [S3] Melhor interpretação de intenção — Issue #22
- `detect_intent` classifica corretamente os seguintes intents:
  - `confirm_yes` / `confirm_no` — confirmação de transação pendente
  - `recent_transactions` — listagem de últimas transações
  - `budget_status` — consulta de orçamento por categoria
  - `invoice_status` — consulta de fatura de cartão
  - `financial_health` — diagnóstico de saúde financeira
  - `card_setup_request` — início de cadastro de cartões
  - `document_request` — envio de extrato ou fatura
  - `summary` — resumo de gastos
  - `transaction` — registro de nova transação (default)
- **Critérios de aceite:**
  - [ ] Cada intent mapeado para o handler correto no webhook.
  - [ ] Intent `invoice_status` detectado em frases como "qual o valor da minha fatura do Nubank?".
  - [ ] Intent `financial_health` detectado em frases como "como está minha saúde financeira?".

---

### S4 — Saúde financeira

#### [S4] Análise de saúde financeira — Issue #23
- O Navi agrega dados de múltiplas fontes para gerar um diagnóstico financeiro:
  - `perfil_financeiro`: saldo estimado, renda identificada
  - `custos_mensais`: custos fixos confirmados
  - `faturas_cartao`: soma das faturas mais recentes por cartão
  - `transacoes`: gastos lançados no mês corrente
- Classifica a situação em 4 níveis: `mais apertada`, `em atenção`, `relativamente equilibrada`, `ainda inconclusiva`.
- **Lógica de classificação com renda conhecida:** razão `(custos_fixos + faturas) / renda` ≥ 100% → apertada; ≥ 70% ou pressão alta → atenção; abaixo → equilibrada.
- **Lógica sem renda:** saldo negativo → apertada; pressão alta ou base comprometida ≥ R$3.000 → atenção.
- **Critérios de aceite:**
  - [ ] Diagnóstico exibe saldo, renda, custos fixos, faturas e gastos do mês quando disponíveis.
  - [ ] Classificação correta para cada combinação de dados.
  - [ ] Mensagem amigável quando não há dados suficientes.

#### [S4] Minha situação financeira está comprometida? — Issue #25
- Acionado pelo intent `financial_health`.
- Responde à pergunta "como está minha saúde financeira?" com o diagnóstico completo.
- **Critérios de aceite:**
  - [ ] Intent detectado para variações como "como está minha vida financeira?", "minha situação financeira", "quão comprometida...".
  - [ ] Resposta inclui status e detalhes dos dados usados no cálculo.

---

### S5 — Experiência avançada

#### [S5] Confirmação antes de salvar dados — Issue #30
- Para transações ambíguas (sem verbo de transação claro + valor explícito), o Navi pede confirmação antes de salvar.
- A transação fica em `confirmacoes_pendentes` até ser confirmada ou rejeitada.
- "SIM" (ou variações) confirma e salva. "NÃO" (ou variações) descarta.
- Apenas uma transação pendente por usuário por vez.
- **Critérios de aceite:**
  - [ ] Transação ambígua gera mensagem de confirmação.
  - [ ] "SIM" salva em `transacoes` e limpa `confirmacoes_pendentes`.
  - [ ] "NÃO" descarta e solicita correção.
  - [ ] Nova transação pendente sobrescreve a anterior.

#### [S5] Suporte a voz — Issue #28
- O Navi aceita mensagens de voz enviadas pelo WhatsApp (formato `audio/ogg; codecs=opus`).
- O áudio é transcrito via OpenAI Whisper (`whisper-1`) em português antes de ser processado como texto normal.
- Se a transcrição falhar, o usuário recebe mensagem orientando a enviar por texto.
- Não há saída de voz — respostas são sempre em texto.
- **Critérios de aceite:**
  - [ ] Mensagem de voz transcrita e processada como texto.
  - [ ] Outros tipos de áudio (mp3, wav, webm, amr) também aceitos.
  - [ ] Falha na transcrição não quebra o fluxo — retorna mensagem de fallback.

#### [S5] Leitura de recibos e imagens — Issue #29
- O Navi aceita imagens de recibos, extratos e faturas enviadas diretamente no WhatsApp.
- Processamento via GPT-4.1-mini com visão multimodal.
- **Critérios de aceite:**
  - [ ] Imagens JPEG, PNG e WebP processadas corretamente.
  - [ ] Tipo do documento inferido por contexto e tipo MIME.
  - [ ] Dados extraídos salvos no perfil financeiro.

---

### S6 — Qualidade e produção

#### [S6] Datas e valores 100% padronizados — Issue #33
- Todas as datas exibidas ao usuário no formato `dd-mm-yyyy`.
- Todos os valores monetários exibidos no formato `R$1.234,56`.
- **Critérios de aceite:**
  - [ ] `format_brl()` aplicada em todos os valores exibidos.
  - [ ] `format_ptbr_date()` aplicada em todas as datas exibidas.

#### [S6] Logs e observabilidade — Issue #34
- Erros críticos logados com contexto suficiente para diagnóstico.
- Endpoint `/health` retorna status do app e do banco.

#### [S6] Segurança e robustez de produção — Issue #35
- Senhas armazenadas com bcrypt.
- JWT com chave secreta configurável por variável de ambiente.
- Admin reset protegido por `X-Admin-Key`.
- Webhook fallback configurado no Twilio para instabilidades.
- **Critérios de aceite:**
  - [ ] Nenhuma senha em texto plano no banco.
  - [ ] `SECRET_KEY` padrão (`change-me`) não deve ir para produção.
  - [ ] `/admin/reset-user` retorna 403 para chave inválida.

#### [S6] Redução de casos ambíguos — Issue #36
- Parsing de saldo não interpreta mensagens sobre cartão/fatura como saldo.
- Parsing de nomes de cartão não aceita saudações ou confirmações como nomes válidos.
- Parsing de quantidade de cartões aceita palavras por extenso além de dígitos.

---

## Detalhamento de Integrações

### Twilio
- Webhook recebe `Form` com `Body`, `From`, `NumMedia`, `MediaUrl0`, `MediaContentType0`.
- Resposta deve ser TwiML: `<Response><Message>texto</Message></Response>`.
- Webhook fallback configurado para retornar mensagem de instabilidade.
- Envio proativo via API REST do Twilio (para notificações fora do ciclo de request/response).

### OpenAI (GPT-4.1-mini)
Usado em 4 contextos diferentes:

| Contexto | Entrada | Saída esperada |
|---|---|---|
| Registro de transação | Texto livre do usuário | `[{tipo, categoria, valor}]` |
| Parsing de nomes de cartão | Frase com nomes + count esperado | `{card_names: [...]}` |
| Análise de extrato (PDF) | Texto extraído pelo pypdf | JSON estruturado com lançamentos, saldo, renda |
| Análise de fatura (imagem) | Base64 da imagem | JSON estruturado com total, vencimento, mínimo |

---

## Regras de Negócio Relevantes

### Categorias de orçamento suportadas
`farmacia`, `mercado`, `alimentacao`, `lazer`, `transporte`, `moradia`, `saude`

### Aliases de categoria
`supermercado` → `mercado`

### Classificação de custos do extrato
- **Fixos:** aluguel, condomínio, mensalidade, seguro, débito automático, telefone, Vivo, Claro, TIM, internet, energia, água, gás, escola, academia, parcela, financiamento.
- **Variáveis:** mercado, supermercado, farmácia, Uber, iFood, restaurante, lazer, PIX enviado, boleto, loterias.

### Validação de renda identificada no extrato
A renda só é persistida se:
- Há evidência explícita de salário na descrição (palavras: "salario", "folha", "proventos", "holerite", "inss", "aposentadoria").
- O tipo de crédito não é investimento, transferência avulsa ou estorno.
- A confiança não é `low`.

### Pressão de cartão
Calculada como razão entre `valor_total_fatura` e `renda_identificada`:
- `alta`: razão ≥ 0,50
- `moderada`: razão ≥ 0,25 ou fatura ≥ R$1.000 sem renda
- `baixa`: demais casos

### Confirmação de transação
Solicitada quando a mensagem **não** tem simultaneamente: verbo de transação claro (`gastei`, `paguei`, `comprei`, `recebi`, `ganhei`, `transferi`) **e** valor explícito no formato `R$XX` ou `XX,XX`.

---

## Variáveis de Ambiente

| Variável | Descrição | Default |
|---|---|---|
| `ACCOUNT_SID` | Twilio Account SID | — |
| `AUTH_TOKEN` | Twilio Auth Token | — |
| `TWILIO_NUMBER` | Número Twilio WhatsApp | — |
| `OPENAI_API_KEY` | Chave da OpenAI | — |
| `SECRET_KEY` | Chave JWT e admin | `change-this-secret` |
| `ALGORITHM` | Algoritmo JWT | `HS256` |
| `DATABASE_HOST` | Host do Postgres | `db` |
| `DATABASE_NAME` | Nome do banco | `navimvp` |
| `DATABASE_USER` | Usuário do banco | `navimvp` |
| `DATABASE_PASSWORD` | Senha do banco | `navimvppw` |
| `DATABASE_PORT` | Porta do banco | `5432` |
| `APP_ENV` | Ambiente (`development`/`production`) | `development` |
| `APP_DEBUG` | Debug mode FastAPI | `false` |
