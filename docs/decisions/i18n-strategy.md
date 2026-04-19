# ADR: Estratégia de i18n — locale por usuário + instrução no prompt do GPT

**Data:** 2026-04-19
**Status:** Aceito
**Issue:** #102

---

## Contexto

Os PRs #100 e #101 introduziram uma função `normalize_ptbr_accents()` com um mapa hardcoded de ~23 palavras PT-BR para corrigir acentuação ausente nas respostas do GPT (ex: `alimentacao → alimentação`). Embora funcione para PT-BR hoje, essa abordagem não escala: cada novo idioma exigiria um mapa próprio, criando dívida técnica proporcional ao número de idiomas suportados.

## Decisão

**Usar locale por usuário + instrução explícita no prompt do GPT.** O GPT já possui capacidade multilíngue nativa — basta instrui-lo corretamente. O backend não deve conter listas de palavras de nenhum idioma.

### Implementação

1. **Coluna `locale`** na tabela `usuarios` (`VARCHAR(10) DEFAULT 'pt-BR'`), migrável via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.
2. **`get_user_locale(user_id)`** em `users.py` retorna o locale do usuário (fallback: `'pt-BR'`).
3. **Instrução no prompt system** de toda chamada GPT que retorna categorias/subcategorias:
   ```
   Idioma do usuário: {locale}.
   Retorne todas as strings com ortografia e acentuação CORRETAS desse idioma.
   ```
4. **Sem pós-processamento** — o campo GPT retorna já deve estar correto. Se errar, ajusta-se o prompt.

## Alternativas consideradas e rejeitadas

| Alternativa | Motivo da rejeição |
|---|---|
| Mapa hardcoded por idioma | Não escala; cada idioma precisa de manutenção separada |
| Biblioteca de localização (i18n lib) | Overhead desnecessário para MVP; GPT resolve o problema nativamente |
| Normalização via unicode/NFC | Não corrige acentos ausentes, apenas normaliza os presentes |
| Pós-processamento com dicionário externo | Mesma dívida do mapa hardcoded, apenas externalizada |

## Quando adicionar novo idioma

1. Permitir o novo valor no campo `locale` (ex: `es-ES`, `fr-FR`, `en-US`).
2. Atualizar esta documentação.
3. Testar com um usuário de teste que o GPT retorna strings corretas para o idioma.
4. Nenhuma linha de código no backend precisa mudar.

## Consequências

- **Positivo:** zero código específico por idioma; expansão internacional sem dívida técnica.
- **Positivo:** GPT lida com edge cases de ortografia que um mapa nunca cobriria completamente.
- **Negativo:** dependência da qualidade do GPT para acentuação; erros ocasionais são possíveis em MVP.
- **Mitigação:** se acentuação do GPT for inconsistente, ajustar o prompt — não adicionar normalização.
