# Orçamentos de Manutenção Predial (Streamlit + Supabase)

Aplicativo web com login para cadastrar orçamentos de manutenção predial e gerar o PDF no modelo da empresa:
logo, dados da empresa, etapas da mão de obra em tópicos, materiais em tópicos ("qtd - item"), valor da mão de obra, valor do material e total.

- `app.py`: o aplicativo Streamlit.
- `assets/logo.png`: logo padrão usada no PDF. Pode ser trocada pela tela "Minha empresa".
- `supabase/schema.sql`: tabelas, regras de segurança (RLS) e bucket privado da logo.
- `supabase/seed_empresa.local.sql`: dados da empresa. Fica fora do Git.
- `public/`: versão HTML alternativa, que não é usada pelo Streamlit.

## 1. Banco (Supabase, projeto "Reformas")
1. No SQL Editor, rode `supabase/schema.sql`.
2. Em **Authentication > Sign In / Providers > Email**:
   - Desative **"Allow new users to sign up"**.
   - Defina a senha mínima com 10 caracteres.
3. Em **Authentication > Users > Invite user**, convide o usuário.
4. Depois que o usuário definir a senha, rode `supabase/seed_empresa.local.sql`, ou preencha a tela "Minha empresa".
5. Em **Authentication > URL Configuration**, coloque a URL do app publicado em *Site URL*.

## 2. Rodar localmente
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\streamlit run app.py
```
As credenciais ficam em `.streamlit/secrets.toml`, que não é versionado.

## 3. Publicar no Streamlit Community Cloud
1. Suba a pasta para um repositório no GitHub (privado é recomendado). O `.gitignore` já deixa de fora os segredos e os dados da empresa.
2. Em share.streamlit.io, clique em **Create app**, escolha o repositório e use `app.py` como arquivo principal.
3. Em **Advanced settings > Secrets**, cole o conteúdo de `.streamlit/secrets.toml`.

## Segurança
- Nenhuma senha ou chave fica no código. As credenciais vêm do `st.secrets`.
- O app usa só a chave **publicável** do Supabase. Nunca use a `service_role`.
- O isolamento dos dados é garantido no banco pelo RLS: cada usuário só acessa os próprios orçamentos e a própria logo.
- Cada sessão do navegador tem o próprio cliente Supabase. Esse cliente nunca é compartilhado via cache, para que um usuário não herde a sessão de outro.
- O login mostra uma mensagem genérica e espera um tempo crescente entre tentativas. O cadastro público fica desativado.
- Os totais são recalculados e validados no banco.
- A exportação CSV é protegida contra injeção de fórmulas.
- A proteção contra requisições forjadas (XSRF) está ativa e os detalhes de erro ficam ocultos para o usuário.
