---
name: DEPLOY
description: used for every deploy used at Clean Cloud.
---

#Roles
Read docs/SPEC.md, then fix [DESCRIBE THE BUG/FEATURE HERE].

Requirements:
- No hardcoded values, patterns, or bank-specific logic
- Bank and locale agnostic
- Delegate interpretation to GPT, not to our code

After fixing:
1. Create GitHub issue: title "[SPRINT] description", labels: type:bug|feature|chore, priority:P0|P1|P2
2. Create a new branch from feature/hom-deploy named fix/description or feat/description
3. Implement the fix on the new branch
4. git add, commit "fix|feat|chore: description", push
5. Open PR from new branch to feature/hom-deploy with "Fixes #ISSUE_NUMBER"
6. Merge PR