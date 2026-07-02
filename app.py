"""
Simulador Interativo de Ocupação da Câmara Fria
=================================================
Une a base de produtos (Cubagem_produto.xlsx) com a base de ocupação
horária (Taxa_de_Ocupação.xlsx). Permite ajustar o crescimento anual
esperado de cada um dos produtos que somam ~90% do volume, e ver o
impacto na projeção do PICO DE OCUPAÇÃO DIÁRIO da câmara em tempo real.

Como rodar localmente:
    pip install -r requirements.txt
    streamlit run app.py

Como rodar no Google Colab:
    !pip install streamlit plotly openpyxl -q
    !wget -q -O - https://loca.lt/mytunnelpassword  # pega o IP/senha do túnel
    !streamlit run app.py &>/content/logs.txt &
    !npx localtunnel --port 8501
    (abra o link impresso pelo localtunnel; a senha é o IP mostrado no wget acima)
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

TOP_N = 20
ANOS_DISPONIVEIS = list(range(2026, 2037))

st.set_page_config(page_title="Simulador de Ocupação — Câmara Fria", layout="wide")
st.title("📊 Simulador de Ocupação da Câmara Fria")
st.caption(
    "Ajuste o crescimento anual esperado de cada produto e veja, em tempo real, "
    "o impacto no pico de ocupação diário projetado."
)

# --------------------------------------------------------------------------
# 1. Upload dos dados
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("1. Dados de entrada")
    file_prod = st.file_uploader("Cubagem_produto.xlsx", type="xlsx")
    file_ocup = st.file_uploader("Taxa_de_Ocupação.xlsx", type="xlsx")
    CAPACIDADE = st.number_input(
        "Capacidade máxima da câmara (cestos)",
        min_value=1, value=2889, step=1,
    )

if not file_prod or not file_ocup:
    st.info("⬅️ Envie os dois arquivos na barra lateral para começar.")
    st.stop()


@st.cache_data
def load_data(f_prod, f_ocup):
    prod_df = pd.read_excel(f_prod, sheet_name="Sheet1")
    ocup_df = pd.read_excel(f_ocup, sheet_name="Sheet1")
    ocup_df["Data"] = pd.to_datetime(ocup_df["Data"], dayfirst=True)
    return prod_df, ocup_df


prod, ocup = load_data(file_prod, file_ocup)

if "snapshots" not in st.session_state:
    st.session_state["snapshots"] = {}

# --------------------------------------------------------------------------
# 2. Preparação: crescimento observado por produto + top N / outros
# --------------------------------------------------------------------------
prod = prod.copy()
prod["crescimento_obs"] = (prod["Qtde Hastes 25"] - prod["Qtde Hastes 24"]) / prod["Qtde Hastes 24"]
mediana_crescimento = prod["crescimento_obs"].median()
prod["crescimento_default"] = prod["crescimento_obs"].fillna(mediana_crescimento)

prod_sorted = prod.sort_values("Qtde Volumes 25", ascending=False).reset_index(drop=True)
top = prod_sorted.head(TOP_N).copy()
outros = prod_sorted.iloc[TOP_N:].copy()

total_cestos_2025 = prod["Qtde Volumes 25"].sum()
top_share = top["Qtde Volumes 25"].sum() / total_cestos_2025

# bucket "Outros" agregado
outros_hastes25 = outros["Qtde Hastes 25"].sum()
outros_hastes24 = outros["Qtde Hastes 24"].sum(skipna=True)
outros_cestos25 = outros["Qtde Volumes 25"].sum()
outros_cubagem25 = outros_hastes25 / outros_cestos25
outros_growth_default = (
    (outros_hastes25 - outros_hastes24) / outros_hastes24
    if outros_hastes24 else mediana_crescimento
)

# --------------------------------------------------------------------------
# 3. Controles: ano + crescimento por produto
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("2. Ano de projeção")
    ano = st.select_slider("Ano alvo", options=ANOS_DISPONIVEIS, value=2026)
    t = ano - 2025

    st.header("3. Crescimento geral")
    st.caption("Aplicado a todos os produtos, em adição ao crescimento individual de cada um.")
    crescimento_geral = st.slider(
        "Crescimento geral",
        min_value=-80.0, max_value=200.0,
        value=0.0, step=1.0, format="%.0f%%",
        key="crescimento_geral",
    ) / 100

    st.header(f"4. Crescimento anual — top {TOP_N} produtos")
    st.caption(f"Juntos somam {top_share:.0%} do volume de 2025. Valor inicial = crescimento observado 2024→2025.")
    growth_inputs = {}
    for _, row in top.iterrows():
        default_pct = float(np.clip(row["crescimento_default"] * 100, -80, 200))
        growth_inputs[row["Espécie"]] = st.slider(
            row["Espécie"],
            min_value=-80.0, max_value=200.0,
            value=round(default_pct, 1), step=1.0, format="%.0f%%",
            key=f"g_{row['Espécie']}",
        ) / 100

    st.header(f"5. Demais {len(outros)} produtos (agregado)")
    st.caption("Cauda longa tratada como um único bloco, com crescimento agregado.")
    outros_pct_default = float(np.clip(outros_growth_default * 100, -80, 200))
    outros_growth = st.slider(
        "Outros produtos (agregado)",
        min_value=-80.0, max_value=200.0,
        value=round(outros_pct_default, 1), step=1.0, format="%.0f%%",
        key="outros_growth",
    ) / 100

    def _reset_sliders():
        for _, r in top.iterrows():
            dp = float(np.clip(r["crescimento_default"] * 100, -80, 200))
            st.session_state[f"g_{r['Espécie']}"] = round(dp, 1)
        st.session_state["crescimento_geral"] = 0.0
        st.session_state["outros_growth"] = round(outros_pct_default, 1)

    st.button("🔄 Restaurar valores observados (2024→2025)", on_click=_reset_sliders)

    if st.session_state["snapshots"]:
        st.divider()
        st.subheader("Anos congelados")
        for fy in sorted(st.session_state["snapshots"].keys()):
            c1, c2 = st.columns([3, 1])
            c1.write(f"✅ {fy}")
            if c2.button("🗑️", key=f"del_{fy}"):
                del st.session_state["snapshots"][fy]
                st.rerun()

# --------------------------------------------------------------------------
# 4. Projeção de cestos por produto (composto até o ano escolhido)
# --------------------------------------------------------------------------
_snaps = st.session_state["snapshots"]
_anos_antes = [y for y in _snaps if y < ano]
ano_base = max(_anos_antes) if _anos_antes else None
snap = _snaps[ano_base] if ano_base else None
t_rel = ano - (ano_base if ano_base else 2025)

_outros_label = f"Outros ({len(outros)} produtos)"
rows = []
for _, row in top.iterrows():
    g = growth_inputs[row["Espécie"]]
    base_h = snap["hastes"].get(row["Espécie"], row["Qtde Hastes 25"]) if snap else row["Qtde Hastes 25"]
    hastes_proj = base_h * (1 + g) ** t_rel * (1 + crescimento_geral) ** t_rel
    cestos_proj = hastes_proj / row["Cubagem 25 (hastes/vol.)"]
    rows.append({
        "produto": row["Espécie"],
        "cestos_2025": row["Qtde Volumes 25"],
        "crescimento_usado": g,
        "cestos_proj": cestos_proj,
        "hastes_proj": hastes_proj,
    })

base_h_outros = snap["hastes_outros"] if snap else outros_hastes25
hastes_proj_outros = base_h_outros * (1 + outros_growth) ** t_rel * (1 + crescimento_geral) ** t_rel
cestos_proj_outros = hastes_proj_outros / outros_cubagem25
rows.append({
    "produto": _outros_label,
    "cestos_2025": outros_cestos25,
    "crescimento_usado": outros_growth,
    "cestos_proj": cestos_proj_outros,
    "hastes_proj": hastes_proj_outros,
})

df_result = pd.DataFrame(rows)
total_cestos_proj = df_result["cestos_proj"].sum()
fator = total_cestos_proj / total_cestos_2025

# --------------------------------------------------------------------------
# 5. Projeção de ocupação (pico diário)
#    Premissa: participação de cada produto na ocupação instantânea ~
#    participação no volume anual. O fator de crescimento total de
#    cestos é aplicado sobre o pico diário real observado em 2025.
# --------------------------------------------------------------------------
daily_peak = ocup.groupby("Data")["Acumulado Total"].max().reset_index()
daily_peak["pico_projetado"] = daily_peak["Acumulado Total"] * fator
daily_peak["pct_capacidade"] = daily_peak["pico_projetado"] / CAPACIDADE

# --------------------------------------------------------------------------
# 6. Métricas e gráfico
# --------------------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Crescimento total de cestos", f"{fator - 1:+.1%}")
col2.metric(f"Total de cestos projetado ({ano})", f"{total_cestos_proj:,.0f}")
pico_max = daily_peak["pico_projetado"].max()
col3.metric("Maior pico diário projetado", f"{pico_max:,.0f}", f"{pico_max / CAPACIDADE:.0%} da capacidade")
dias_estourando = int((daily_peak["pico_projetado"] > CAPACIDADE).sum())
col4.metric("Dias acima da capacidade", f"{dias_estourando} de {len(daily_peak)}")

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=daily_peak["Data"], y=daily_peak["Acumulado Total"],
    name="Pico real 2025", line=dict(color="gray", dash="dot"),
))
fig.add_trace(go.Scatter(
    x=daily_peak["Data"], y=daily_peak["pico_projetado"],
    name=f"Pico projetado {ano}", line=dict(color="#1f77b4", width=2),
    fill="tonexty",
))
fig.add_hline(
    y=CAPACIDADE, line_dash="dash", line_color="red",
    annotation_text=f"Capacidade ({CAPACIDADE} cestos)", annotation_position="top left",
)
fig.update_layout(
    title=f"Pico de ocupação diário — {ano} projetado vs. 2025 real",
    xaxis_title="Dia do ano (eixo herdado do calendário 2025)",
    yaxis_title="Cestos (pico do dia)",
    hovermode="x unified", height=520,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------
# Congelar simulação
# --------------------------------------------------------------------------
base_label = f"{ano_base} (congelado)" if ano_base else "2025 (real)"
st.info(f"Base desta projeção: **{base_label}** — {t_rel} ano(s) de crescimento aplicados.")

def _congelar():
    st.session_state["snapshots"][ano] = {
        "hastes": {r["produto"]: r["hastes_proj"] for r in rows if r["produto"] != _outros_label},
        "hastes_outros": next(r["hastes_proj"] for r in rows if r["produto"] == _outros_label),
    }

col_btn, _ = st.columns([2, 3])
col_btn.button(f"🔒 Congelar simulação {ano}", on_click=_congelar, type="primary")

# --------------------------------------------------------------------------
# 7. Detalhamento por produto
# --------------------------------------------------------------------------
st.subheader("Detalhamento por produto")
df_show = df_result.copy()
df_show["participação 2025"] = df_show["cestos_2025"] / total_cestos_2025
df_show["cestos_2025"] = df_show["cestos_2025"].round(0).astype(int)
df_show["cestos_proj"] = df_show["cestos_proj"].round(0).astype(int)
df_show = df_show.rename(columns={
    "produto": "Produto", "cestos_2025": "Cestos 2025 (real)",
    "crescimento_usado": f"Crescimento até {ano}", "cestos_proj": f"Cestos {ano} (projetado)",
})
st.dataframe(
    df_show.style.format({f"Crescimento até {ano}": "{:+.1%}", "participação 2025": "{:.1%}"}),
    use_container_width=True, hide_index=True,
)

st.caption(
    "**Premissas:** cada produto usa sua própria cubagem de 2025 (hastes/cesto) como taxa fixa de conversão. "
    "A ocupação diária é projetada aplicando o fator de crescimento total de cestos sobre o pico real "
    "observado em cada dia de 2025 — ou seja, assume-se giro médio parecido entre produtos na câmara "
    "(sem dado de tempo de residência por produto). "
    f"A base de 2025 tem registro em {len(daily_peak)} dos 365 dias do ano."
)
