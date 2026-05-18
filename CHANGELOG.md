# Changelog

## [Unreleased]

### S2A — Budgets
- Cadastro de budgets por categoria com suporte a 8 categorias (`farmacia`, `mercado`, `alimentacao`, `lazer`, `transporte`, `moradia`, `saude`, `supermercado`)
- Sistema de alertas de consumo com 3 níveis (50% / 80% / 100%), idempotente por mês/categoria

### S2B — Documentos
- Armazenamento e análise inicial de documentos (PDF, JPEG, PNG, WebP) com processamento em background e acesso privado por usuário
- Dados extraídos de documentos usados em respostas e análises (`perfil_financeiro`, `faturas_cartao`)

### S2C — Cartões & Saldo
- Cadastro de saldo ou extrato inicial no onboarding com parsing de formatos "R$ 3200", "3.200,00"
- Leitura e categorização de custos iniciais com revisão e ajuste pelo usuário
- Vínculo automático de faturas aos cartões cadastrados via `documentos_financeiros.cartao_id`

### S3 — Conversa Inteligente
- Follow-up contextual: reconhece continuações como "e da Nubank?" dentro do mesmo tópico
- Eliminação de padrões de chatbot de menu; todas as respostas em linguagem natural
- Interpretação de intenção com 9+ intents mapeados via `detect_intent()`

### S4 — Saúde Financeira
- Análise de saúde financeira completa: classifica situação em apertada / atenção / equilibrada
- Avaliação de comprometimento de renda com base em renda, custos fixos e faturas

### S5 — Experiência Avançada
- Leitura de recibos e imagens via GPT-4.1-mini multimodal (JPEG, PNG, WebP)
- Confirmação antes de salvar dados: toda transação requer confirmação antes de persistir

### S6 — Qualidade
- Formatação padronizada de datas (dd-mm-yyyy) e valores monetários (R$1.234,56) em todo o sistema
- Segurança: bcrypt + JWT, queries parametrizadas, validação X-Admin-Key, configuração de SECRET_KEY
- Redução de casos ambíguos: parsing defensivo em balance, card names, confirmações e intents
