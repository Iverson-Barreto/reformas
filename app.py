"""Orçamentos de Manutenção Predial — Streamlit + Supabase.

Segurança:
- Credenciais lidas de st.secrets (nunca no código / Git).
- Somente a chave PUBLICÁVEL do Supabase é usada; os dados são isolados por RLS no banco.
- Um cliente Supabase POR SESSÃO de navegador (st.session_state), nunca compartilhado
  via cache entre usuários.
- Senhas não são armazenadas pelo app; o Supabase Auth guarda apenas o hash.
"""
from __future__ import annotations

import io
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st
from fpdf import FPDF
from supabase import Client, create_client

st.set_page_config(page_title="Orçamentos Prediais", page_icon="🏢", layout="wide")

CATEGORIAS = ["Elétrica", "Hidráulica", "Civil", "Pintura", "Elevadores", "Ar-condicionado",
              "Incêndio", "Jardinagem", "Limpeza", "Outros"]
STATUS = ["Pendente", "Aprovado", "Reprovado", "Em execução", "Concluído"]
TIPOS_LOGO = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
LOGO_PADRAO = Path(__file__).parent / "assets" / "logo.png"


def brl(v) -> str:
    s = f"{float(v or 0):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def data_br(d) -> str:
    if not d:
        return ""
    return datetime.fromisoformat(str(d)[:10]).strftime("%d/%m/%Y")


# ---------------- Cliente por sessão ----------------
def sb() -> Client:
    if "sb" not in st.session_state:
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_PUBLISHABLE_KEY"]
        except Exception:
            st.error("Configuração ausente: defina SUPABASE_URL e SUPABASE_PUBLISHABLE_KEY em secrets.")
            st.stop()
        st.session_state.sb = create_client(url, key)
    return st.session_state.sb


def com_retry(fn, tentativas: int = 3):
    """Executa uma chamada ao Supabase com novas tentativas em falhas de rede."""
    for i in range(tentativas):
        try:
            return fn()
        except Exception as e:
            rede = type(e).__module__.startswith(("httpx", "httpcore")) or "timeout" in type(e).__name__.lower()
            if not rede:
                raise
            if i == tentativas - 1:
                st.error("Não foi possível conectar ao servidor. Verifique a internet e tente novamente.")
                if st.button("🔄 Tentar novamente"):
                    st.rerun()
                st.stop()
            time.sleep(1.5 * (i + 1))


def usuario():
    return st.session_state.get("user")


# ---------------- Login ----------------
def tela_login():
    st.title("🏢 Orçamentos Prediais")
    col, _ = st.columns([1, 2])
    with col:
        bloqueio = st.session_state.get("bloqueado_ate", 0)
        with st.form("login"):
            email = st.text_input("E-mail", max_chars=254)
            senha = st.text_input("Senha", type="password")
            entrar = st.form_submit_button("Entrar", use_container_width=True)
        if entrar:
            if time.time() < bloqueio:
                st.error(f"Muitas tentativas. Aguarde {int(bloqueio - time.time())} s.")
                return
            try:
                res = sb().auth.sign_in_with_password({"email": email.strip(), "password": senha})
                st.session_state.user = res.user
                st.session_state.tentativas = 0
                st.rerun()
            except Exception:
                n = st.session_state.get("tentativas", 0) + 1
                st.session_state.tentativas = n
                st.session_state.bloqueado_ate = time.time() + min(2 ** n, 60)
                st.error("E-mail ou senha inválidos.")  # genérica: não revela se o e-mail existe

        with st.expander("Esqueci minha senha"):
            em = st.text_input("Seu e-mail", key="rec_email", max_chars=254)
            if st.button("Enviar link de redefinição") and em:
                try:
                    sb().auth.reset_password_for_email(em.strip())
                except Exception:
                    pass
                st.info("Se o e-mail estiver cadastrado, você receberá um link.")


def sair():
    try:
        sb().auth.sign_out()
    finally:
        st.session_state.clear()
        st.rerun()


# ---------------- Empresa ----------------
def carregar_empresa() -> dict:
    res = com_retry(lambda: sb().table("empresa").select("*").limit(1).execute())
    return res.data[0] if res.data else {}


def baixar_logo(path: str | None) -> bytes | None:
    """Logo enviada pelo usuário; se não houver, usa a logo padrão em assets/logo.png."""
    if path:
        try:
            return sb().storage.from_("logos").download(path)
        except Exception:
            pass
    return LOGO_PADRAO.read_bytes() if LOGO_PADRAO.exists() else None


def aba_empresa(emp: dict):
    st.subheader("Dados da empresa")
    logo = baixar_logo(emp.get("logo_path"))
    if logo:
        st.image(logo, width=180)
    with st.form("empresa"):
        razao = st.text_input("Razão social / Nome*", emp.get("razao", ""), max_chars=150)
        c1, c2 = st.columns(2)
        cnpj = c1.text_input("CNPJ", emp.get("cnpj") or "", max_chars=18)
        tel = c2.text_input("Telefone", emp.get("telefone") or "", max_chars=30)
        email = c1.text_input("E-mail", emp.get("email") or "", max_chars=254)
        end = st.text_input("Endereço", emp.get("endereco") or "", max_chars=250)
        arq = st.file_uploader("Logomarca (PNG, JPG ou WEBP, até 1 MB)", type=["png", "jpg", "jpeg", "webp"])
        ok = st.form_submit_button("Salvar")
    if not ok:
        return
    if not razao.strip():
        st.error("Informe a razão social.")
        return
    dados = {"razao": razao.strip(), "cnpj": cnpj.strip() or None, "telefone": tel.strip() or None,
             "email": email.strip() or None, "endereco": end.strip() or None,
             "logo_path": emp.get("logo_path"), "user_id": usuario().id}
    if arq:
        ext = TIPOS_LOGO.get(arq.type)
        if not ext or arq.size > 1024 * 1024:
            st.error("Logo inválida: use PNG/JPG/WEBP de até 1 MB.")
            return
        caminho = f"{usuario().id}/logo-{uuid.uuid4().hex}.{ext}"  # pasta do usuário (RLS do Storage)
        try:
            sb().storage.from_("logos").upload(caminho, arq.getvalue(), {"content-type": arq.type})
            if emp.get("logo_path"):
                sb().storage.from_("logos").remove([emp["logo_path"]])
        except Exception as e:
            st.error(f"Erro ao enviar logo: {e}")
            return
        dados["logo_path"] = caminho
    try:
        sb().table("empresa").upsert(dados).execute()
        st.success("Dados da empresa salvos.")
        st.rerun()
    except Exception as e:
        st.error(f"Erro ao salvar: {e}")


# ---------------- PDF ----------------
def _t(s) -> str:
    """Fontes padrão do PDF usam latin-1; troca caracteres fora dele."""
    return str(s or "").replace("—", "-").replace("–", "-").encode("latin-1", "replace").decode("latin-1")


def num_br(v) -> str:
    return brl(v).replace("R$ ", "")


def gerar_pdf(o: dict, emp: dict) -> bytes:
    """Layout do modelo do cliente: logo, título, dados da empresa, mão de obra em tópicos,
    material em tópicos (qtd - item), valores e total."""
    cat = o["categoria"].lower()
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 18, 20)
    pdf.set_auto_page_break(True, 18)
    pdf.add_page()

    def titulo(txt):
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, _t(txt), new_x="LMARGIN", new_y="NEXT")

    def linha(txt, bold=False):
        pdf.set_font("Helvetica", "B" if bold else "", 12)
        pdf.cell(0, 6.5, _t(txt), new_x="LMARGIN", new_y="NEXT")

    def topico(txt):
        pdf.set_font("Helvetica", "", 12)
        pdf.set_x(28)
        pdf.cell(6, 6.5, chr(149))  # marcador "•"
        pdf.multi_cell(0, 6.5, _t(txt), new_x="LMARGIN", new_y="NEXT")

    # Logo no canto superior direito
    logo = baixar_logo(emp.get("logo_path"))
    if logo:
        try:
            pdf.image(io.BytesIO(logo), x=160, y=14, w=30)
        except Exception:
            pass

    titulo(f"Orçamento de {cat}")
    pdf.ln(4)
    linha(emp.get("razao") or "")
    if emp.get("telefone"):
        linha(f"Celular: {emp['telefone']}")
    if emp.get("cnpj"):
        linha(f"CNPJ: {emp['cnpj']}")
    if emp.get("endereco"):
        linha(f"Endereço: {emp['endereco']}")
    if emp.get("email"):
        linha(f"E-mail: {emp['email']}")
    pdf.ln(3)
    cliente = o["edificio"] + (f" - {o['local']}" if o.get("local") else "")
    linha(f"Cliente: {cliente}")
    linha(f"Data: {data_br(o['created_at'])}")

    pdf.ln(5)
    titulo(f"Orçamento de mão de obra de {cat}")
    pdf.ln(2)
    for etapa in [l.strip(" -•\t") for l in o["descricao"].splitlines()]:
        if etapa:
            topico(etapa)
    linha(f"Valor mão de obra - {num_br(o['mao_de_obra'])}")

    mats = o.get("materiais") or []
    if mats:
        pdf.ln(5)
        titulo("Orçamento do material")
        pdf.ln(2)
        for m in mats:
            q = f"{float(m['qtd']):g}".replace(".", ",")
            un = f" {m['unidade']}" if m.get("unidade") and m["unidade"] != "un" else ""
            topico(f"{q}{un} - {m['descricao']}")
        linha(f"Valor do material - {num_br(o['valor_materiais'])}", bold=True)

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, _t(f"Valor total do orçamento: {num_br(o['valor'])}"), new_x="LMARGIN", new_y="NEXT")
    if o.get("prazo"):
        linha(f"Prazo: {data_br(o['prazo'])}")
    return bytes(pdf.output())


# ---------------- Orçamentos ----------------
def carregar_orcamentos() -> list[dict]:
    return com_retry(lambda: sb().table("orcamentos").select("*").order("created_at", desc=True).execute()).data or []


def form_orcamento(o: dict | None):
    o = o or {}
    editando = bool(o.get("id"))
    st.subheader("Editar orçamento" if editando else "Novo orçamento")
    k = o.get("id", "novo")  # chaves únicas por orçamento
    c1, c2, c3 = st.columns(3)
    edificio = c1.text_input("Edifício / Condomínio (cliente)*", o.get("edificio", ""), max_chars=120, key=f"ed{k}")
    local = c2.text_input("Local (bloco, andar, área)", o.get("local") or "", max_chars=120, key=f"lo{k}")
    categoria = c3.selectbox("Categoria*", CATEGORIAS, CATEGORIAS.index(o.get("categoria", "Elétrica")), key=f"ca{k}")
    fornecedor = c1.text_input("Responsável / Fornecedor", o.get("fornecedor") or "", max_chars=120, key=f"fo{k}")
    prazo = c2.date_input("Prazo", date.fromisoformat(o["prazo"]) if o.get("prazo") else None, format="DD/MM/YYYY", key=f"pr{k}")
    status = c3.selectbox("Status", STATUS, STATUS.index(o.get("status", "Pendente")), key=f"st{k}")
    descricao = st.text_area("Etapas da mão de obra* (uma por linha — viram tópicos no orçamento)",
                             o.get("descricao", ""), max_chars=2000, height=160, key=f"de{k}",
                             placeholder="Isolamento do ambiente\nRaspagem da infiltração\nPintura fundo preparador")

    st.markdown("**Materiais** — o valor por item é opcional; se não informar, use o *valor total do material* abaixo.")
    df = pd.DataFrame(o.get("materiais") or [], columns=["descricao", "unidade", "qtd", "valor_unit"])
    mats = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, key=f"ma{k}",
        column_config={
            "descricao": st.column_config.TextColumn("Material", max_chars=200, required=True),
            "unidade": st.column_config.TextColumn("Unid.", max_chars=10, default="un"),
            "qtd": st.column_config.NumberColumn("Qtd.", min_value=0.001, step=0.001, default=1.0, required=True),
            "valor_unit": st.column_config.NumberColumn("Valor unit. (R$, opcional)", min_value=0.0, step=0.01, format="%.2f", default=0.0),
        })
    mats = mats.dropna(subset=["descricao"])
    mats = mats[mats["descricao"].astype(str).str.strip() != ""]
    mats["valor_unit"] = mats["valor_unit"].fillna(0)
    soma_itens = float((mats["qtd"].fillna(0) * mats["valor_unit"]).round(2).sum()) if len(mats) else 0.0

    c1, c2 = st.columns(2)
    mao = c1.number_input("Valor mão de obra (R$)*", min_value=0.0, step=0.01, value=float(o.get("mao_de_obra") or 0), format="%.2f", key=f"mo{k}")
    mat_inf = c2.number_input("Valor total do material (R$)", min_value=0.0, step=0.01,
                              value=float(o.get("valor_material_informado") or 0), format="%.2f", key=f"mi{k}",
                              disabled=soma_itens > 0, help="Ignorado quando os itens têm valor unitário.")
    tot_mat = soma_itens if soma_itens > 0 else mat_inf
    c1, c2, c3 = st.columns(3)
    c1.metric("Material", brl(tot_mat))
    c2.metric("Mão de obra", brl(mao))
    c3.metric("Total do orçamento", brl(tot_mat + mao))

    b1, b2, _ = st.columns([1, 1, 4])
    if b1.button("💾 Salvar", type="primary", key=f"sv{k}"):
        if not edificio.strip() or not descricao.strip():
            st.error("Preencha edifício e descrição.")
            return
        if (mats["qtd"].fillna(0) <= 0).any() or (mats["valor_unit"] < 0).any():
            st.error("Verifique os materiais: quantidade > 0 e valor ≥ 0.")
            return
        dados = {
            "edificio": edificio.strip(), "local": local.strip() or None, "categoria": categoria,
            "fornecedor": fornecedor.strip() or None, "prazo": prazo.isoformat() if prazo else None,
            "status": status, "descricao": descricao.strip(), "mao_de_obra": round(mao, 2),
            "valor_material_informado": round(mat_inf, 2) if soma_itens == 0 else None,
            "materiais": [{"descricao": str(r.descricao).strip(), "unidade": str(r.unidade or "").strip(),
                           "qtd": float(r.qtd), "valor_unit": round(float(r.valor_unit), 2)}
                          for r in mats.itertuples()],
        }  # totais são recalculados no banco
        try:
            if editando:
                sb().table("orcamentos").update(dados).eq("id", o["id"]).execute()
            else:
                sb().table("orcamentos").insert(dados).execute()
            st.session_state.pop("editar", None)
            st.toast("Orçamento salvo.")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao salvar: {e}")
    if editando and b2.button("Cancelar", key=f"cc{k}"):
        st.session_state.pop("editar", None)
        st.rerun()


def nome_pdf(o: dict) -> str:
    base = "".join(c if c.isalnum() else "-" for c in f"orcamento-{o['categoria']}-{o['edificio']}".lower())
    return base[:60].strip("-") + ".pdf"


def link_compartilhar(o: dict, pdf_bytes: bytes) -> str | None:
    """Salva o PDF no bucket privado e retorna um link assinado válido por 7 dias."""
    caminho = f"{usuario().id}/{o['id']}.pdf"  # pasta do usuário (RLS do Storage)
    try:
        com_retry(lambda: sb().storage.from_("pdfs").upload(
            caminho, pdf_bytes, {"content-type": "application/pdf", "upsert": "true"}))
        res = com_retry(lambda: sb().storage.from_("pdfs").create_signed_url(
            caminho, 7 * 24 * 3600, {"download": nome_pdf(o)}))
        return res.get("signedURL") or res.get("signedUrl")
    except Exception as e:
        st.error(f"Não foi possível gerar o link: {e}")
        return None


def csv_seguro(df: pd.DataFrame) -> bytes:
    def esc(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # evita injeção de fórmula
    return df.map(esc).to_csv(index=False, sep=";").encode("utf-8-sig")


def aba_orcamentos(emp: dict):
    orcs = carregar_orcamentos()
    soma = lambda f: sum(float(o["valor"]) for o in orcs if f(o))
    c = st.columns(4)
    c[0].metric("Total cadastrado", brl(soma(lambda o: True)))
    c[1].metric("Pendentes", brl(soma(lambda o: o["status"] == "Pendente")))
    c[2].metric("Aprovados / em execução", brl(soma(lambda o: o["status"] in ("Aprovado", "Em execução"))))
    c[3].metric("Concluídos", brl(soma(lambda o: o["status"] == "Concluído")))

    with st.container(border=True):
        alvo = next((o for o in orcs if o["id"] == st.session_state.get("editar")), None)
        form_orcamento(alvo)

    st.subheader("Orçamentos")
    f1, f2 = st.columns([3, 1])
    busca = f1.text_input("Buscar", placeholder="Edifício, descrição, material…", max_chars=100).lower()
    fst = f2.selectbox("Status", ["Todos"] + STATUS)
    lista = [o for o in orcs if (fst == "Todos" or o["status"] == fst) and (not busca or any(
        busca in str(x or "").lower() for x in
        [o["edificio"], o.get("local"), o["descricao"], o.get("fornecedor"), o["categoria"],
         *[m["descricao"] for m in o.get("materiais") or []]]))]

    if not lista:
        st.info("Nenhum orçamento encontrado.")
        return

    for o in lista:
        titulo = f"**{o['edificio']}** · {o['categoria']} · {brl(o['valor'])} · {o['status']} · {data_br(o['created_at'])}"
        with st.expander(titulo):
            st.write(o["descricao"])
            st.caption(f"Materiais: {brl(o['valor_materiais'])} · Mão de obra: {brl(o['mao_de_obra'])} · Prazo: {data_br(o.get('prazo')) or '-'}")
            b1, b4, b2, b3, _ = st.columns([1, 1, 1, 1, 2])
            pdf_bytes = gerar_pdf(o, emp)
            b1.download_button("📄 Baixar PDF", pdf_bytes, file_name=nome_pdf(o),
                               mime="application/pdf", key=f"pdf{o['id']}")
            if b4.button("📲 Compartilhar", key=f"sh{o['id']}"):
                st.session_state[f"link{o['id']}"] = link_compartilhar(o, pdf_bytes)
            link = st.session_state.get(f"link{o['id']}")
            if link:
                msg_wa = f"Olá! Segue o orçamento de {o['categoria'].lower()} - {o['edificio']}: {link}"
                st.link_button("🟢 Enviar pelo WhatsApp", "https://wa.me/?text=" + quote(msg_wa))
                st.caption("Ou copie o link (válido por 7 dias):")
                st.code(link, language=None)
            if b2.button("✏️ Editar", key=f"e{o['id']}"):
                st.session_state.editar = o["id"]
                st.rerun()
            if b3.button("🗑️ Excluir", key=f"x{o['id']}"):
                st.session_state.confirmar = o["id"]
            if st.session_state.get("confirmar") == o["id"]:
                st.warning("Confirma a exclusão deste orçamento?")
                if st.button("Sim, excluir", key=f"cx{o['id']}", type="primary"):
                    sb().table("orcamentos").delete().eq("id", o["id"]).execute()
                    st.session_state.pop("confirmar", None)
                    st.rerun()

    df = pd.DataFrame([{**{k: o.get(k) for k in ["edificio", "local", "categoria", "descricao", "fornecedor",
                                                  "valor_materiais", "mao_de_obra", "valor", "prazo", "status", "created_at"]},
                        "materiais": " | ".join(f"{m['qtd']} {m.get('unidade', '')} {m['descricao']} @ {m['valor_unit']}"
                                                for m in o.get("materiais") or [])} for o in lista])
    st.download_button("⬇️ Exportar CSV", csv_seguro(df), file_name=f"orcamentos-{date.today()}.csv", mime="text/csv")


# ---------------- Main ----------------
def main():
    if not usuario():
        tela_login()
        return
    emp = carregar_empresa()
    with st.sidebar:
        logo = baixar_logo(emp.get("logo_path"))
        if logo:
            st.image(logo, width=160)
        st.markdown(f"### {emp.get('razao') or 'Orçamentos Prediais'}")
        st.caption(usuario().email)
        if st.button("Sair", use_container_width=True):
            sair()
    if not emp:
        st.warning("Cadastre os dados da sua empresa na aba **Minha empresa**.")
    t1, t2 = st.tabs(["📋 Orçamentos", "🏢 Minha empresa"])
    with t1:
        aba_orcamentos(emp)
    with t2:
        aba_empresa(emp)


main()
