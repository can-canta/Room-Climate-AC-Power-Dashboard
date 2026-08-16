import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- ページ設定 ---
st.set_page_config(page_title="Climate Dashboard", layout="wide", initial_sidebar_state="collapsed")

# メトリクスのフォント縮小 & delta矢印の非表示
st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.5rem; line-height: 1.3; }
[data-testid="stMetricLabel"] p { font-size: 0.75rem; }
[data-testid="stMetricDelta"] { font-size: 0.7rem; }
[data-testid="stMetricDelta"] svg { display: none; }
</style>
""", unsafe_allow_html=True)

st.title("🌡️ Room Climate & AC Power Dashboard")

# ============================================================
# ログシート設定
#   Log    : 旧 recordRemoData が書いた日本語ヘッダ9列
#   LogV2  : 新 monitor() が書く英語ヘッダ12列（watts/compressor付き）
# ============================================================
SPREADSHEET_ID = "1pxrZn1wqqdS2K31r4T84V8bjK9emGwTMQMLtd70tnmg"
SHEETS = ["Log", "LogV2"]

# ============================================================
# 関西電力 従量電灯A 単価（2026年8月時点・税込）
# 燃料費調整単価は毎月、再エネ賦課金は毎年5月に改定
# 要更新: https://kepco.jp/ryokin/unitprice/ju_a_hayami/
#   TIER_RATE  15-120kWh: 20.21 / 120-300kWh: 25.61 / 300kWh超: 28.59
# ============================================================
KEPCO_TIER_RATE = 28.59
KEPCO_FUEL_ADJ = -1.26
KEPCO_RENEWABLE_LEVY = 4.18
UNIT_PRICE = KEPCO_TIER_RATE + KEPCO_FUEL_ADJ + KEPCO_RENEWABLE_LEVY  # 円/kWh

COMPRESSOR_THRESHOLD_W = 300   # 送風30W / 稼働600W の中間
MAX_GAP_HOURS = 0.5            # これを超える欠測は積分対象外

TEMP_AXIS_MIN, TEMP_AXIS_MAX = 15, 40

COLUMN_MAP = {
    "time": "timestamp", "timestamp": "timestamp",
    "室内温度(℃)": "roomTemp", "roomTemp": "roomTemp",
    "室内湿度(%)": "roomHum", "roomHum": "roomHum",
    "設定温度(℃)": "acSetTemp", "acSetTemp": "acSetTemp",
    "電源状態": "acPowerOn", "acPowerOn": "acPowerOn",
    "室外気温(℃)": "outTemp", "outTemp": "outTemp",
    "室外湿度(%)": "outHum", "outHum": "outHum",
    "雨量(mm)": "outPrecip", "outPrecip": "outPrecip",
    "天気": "weatherCode", "weatherCode": "weatherCode",
    "watts": "watts", "compressor": "compressor", "action": "action",
}

NUMERIC_COLS = ["watts", "compressor", "roomTemp", "roomHum", "acSetTemp",
                "outTemp", "outHum", "outPrecip", "weatherCode"]

WEATHER_CODE_MAP = {
    0: "☀️ 快晴", 1: "🌤️ 晴れ", 2: "⛅ 一部曇り", 3: "☁️ 曇り",
    45: "🌫️ 霧", 48: "🌫️ 霧氷",
    51: "🌦️ 霧雨(弱)", 53: "🌦️ 霧雨(中)", 55: "🌦️ 霧雨(強)",
    56: "🌧️ 着氷性霧雨(弱)", 57: "🌧️ 着氷性霧雨(強)",
    61: "🌧️ 雨(弱)", 63: "🌧️ 雨(中)", 65: "🌧️ 雨(強)",
    66: "🌧️ 着氷性の雨(弱)", 67: "🌧️ 着氷性の雨(強)",
    71: "🌨️ 雪(弱)", 73: "🌨️ 雪(中)", 75: "🌨️ 雪(強)", 77: "🌨️ 細氷",
    80: "🌦️ にわか雨(弱)", 81: "🌦️ にわか雨(中)", 82: "⛈️ にわか雨(強)",
    85: "🌨️ にわか雪(弱)", 86: "🌨️ にわか雪(強)",
    95: "⛈️ 雷雨", 96: "⛈️ 雷雨(雹・弱)", 99: "⛈️ 雷雨(雹・強)",
}

WBGT_LEVELS = [(21, "ほぼ安全"), (25, "注意"), (28, "警戒"), (31, "厳重警戒"), (99, "危険")]


def csv_url(sheet_name: str) -> str:
    return (f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}"
            f"/gviz/tq?tqx=out:csv&sheet={sheet_name}")


def calc_wbgt(temp, humidity):
    """屋内WBGT近似（日本生気象学会式・輻射熱なし）"""
    e = humidity / 100 * 6.105 * np.exp(17.27 * temp / (237.7 + temp))
    return 0.567 * temp + 0.393 * e + 3.94


def wbgt_level(value):
    if pd.isna(value):
        return "N/A"
    for threshold, label in WBGT_LEVELS:
        if value < threshold:
            return label
    return "危険"


def fmt(value, unit, digits=1):
    return f"{value:.{digits}f} {unit}" if pd.notna(value) else "N/A"


def latest_valid(series):
    """末尾から遡って最初の有効値を返す（シート交互混在への耐性）"""
    s = series.dropna()
    return s.iloc[-1] if len(s) else np.nan


@st.cache_data(ttl=300)
def load_log() -> pd.DataFrame:
    frames, errors = [], []

    for name in SHEETS:
        try:
            raw = pd.read_csv(csv_url(name))
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue

        raw = raw.rename(columns={c: COLUMN_MAP.get(str(c).strip(), str(c).strip())
                                  for c in raw.columns})
        if "timestamp" not in raw.columns:
            errors.append(f"{name}: 時刻列が見つかりません（列名: {list(raw.columns)}）")
            continue

        frames.append(raw)

    if not frames:
        raise ValueError(" / ".join(errors) if errors else "全シートが空です")

    df = pd.concat(frames, ignore_index=True)

    for col in set(COLUMN_MAP.values()):
        if col not in df.columns:
            df[col] = pd.NA

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", format="mixed")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    df["acPowerOn"] = df["acPowerOn"].replace({"ON": 1, "OFF": 0})

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

    df["roomWBGT"] = calc_wbgt(df["roomTemp"], df["roomHum"])
    df["outWBGT"] = calc_wbgt(df["outTemp"], df["outHum"])
    df["weatherLabel"] = df["weatherCode"].map(
        lambda c: WEATHER_CODE_MAP.get(int(c), f"不明({int(c)})") if pd.notna(c) else "N/A"
    )

    # compressor未記録の行はwattsから再判定
    df["compressor"] = df["compressor"].fillna(
        (df["watts"] > COMPRESSOR_THRESHOLD_W).astype(float).where(df["watts"].notna())
    )

    # --- 電力量・電気代 ---
    # watts欠損行（旧シート由来）を挟むと台形積分が破綻するため、
    # watts保持行のみを抽出した部分系列上で積分し、元のindexへ書き戻す
    df["kwh"] = np.nan
    df["cost_yen"] = np.nan

    p = df.loc[df["watts"].notna(), ["timestamp", "watts"]].copy()
    if len(p) >= 2:
        dt_h = p["timestamp"].diff().dt.total_seconds() / 3600
        dt_h = dt_h.where(dt_h <= MAX_GAP_HOURS)
        watts_avg = (p["watts"] + p["watts"].shift(1)) / 2
        kwh = (watts_avg * dt_h / 1000).clip(lower=0)
        df.loc[p.index, "kwh"] = kwh
        df.loc[p.index, "cost_yen"] = kwh * UNIT_PRICE

    return df


try:
    df = load_log()
except Exception as e:
    st.error(f"ログの読み込みに失敗しました: {e}")
    st.stop()

if df.empty:
    st.warning("ログデータがまだありません。GAS側のトリガーが動作しているか確認してください。")
    st.stop()

period = st.radio("表示期間", ["24時間", "3日", "7日", "全期間"],
                  index=1, horizontal=True, label_visibility="collapsed")
hours = {"24時間": 24, "3日": 72, "7日": 168}.get(period)
if hours:
    df_view = df[df["timestamp"] >= df["timestamp"].max() - pd.Timedelta(hours=hours)].copy()
else:
    df_view = df.copy()

has_power = df["watts"].notna().any()

# ============================================================
# 1. 環境ステータス
# ============================================================
st.subheader("環境ステータス")

room_temp = latest_valid(df["roomTemp"])
room_hum = latest_valid(df["roomHum"])
room_wbgt = latest_valid(df["roomWBGT"])
ac_set = latest_valid(df["acSetTemp"])
out_temp = latest_valid(df["outTemp"])
out_hum = latest_valid(df["outHum"])
out_wbgt = latest_valid(df["outWBGT"])
out_precip = latest_valid(df["outPrecip"])
weather_label = df["weatherLabel"].replace("N/A", np.nan).dropna()
weather_label = weather_label.iloc[-1] if len(weather_label) else "N/A"

c1, c2, c3, c4 = st.columns(4)
delta_temp = None
if pd.notna(room_temp) and pd.notna(out_temp):
    delta_temp = f"{room_temp - out_temp:.1f} ℃ (外気差)"

c1.metric("室内温度", fmt(room_temp, "℃"), delta=delta_temp)
c2.metric("室内湿度", fmt(room_hum, "%"))
c3.metric("室内WBGT", fmt(room_wbgt, ""), delta=wbgt_level(room_wbgt), delta_color="off")
c4.metric("設定温度", fmt(ac_set, "℃", digits=0))

c5, c6, c7, c8 = st.columns(4)
c5.metric("室外気温", fmt(out_temp, "℃"))
c6.metric("室外湿度", fmt(out_hum, "%"))
c7.metric("室外WBGT", fmt(out_wbgt, ""), delta=wbgt_level(out_wbgt), delta_color="off")
c8.metric("天気", weather_label, delta=fmt(out_precip, "mm") + " 雨量", delta_color="off")

# ============================================================
# 2. 温湿度・WBGT推移（最上位グラフ）
# ============================================================
st.subheader("温湿度・WBGT推移")

fig = make_subplots(specs=[[{"secondary_y": True}]])

# コンプレッサー稼働帯（矩形を大量生成せずトレース1本で描画）
if df_view["compressor"].notna().any():
    fig.add_trace(go.Scatter(
        x=df_view["timestamp"],
        y=TEMP_AXIS_MIN + df_view["compressor"] * (TEMP_AXIS_MAX - TEMP_AXIS_MIN),
        name="コンプレッサー稼働",
        line=dict(color="rgba(0,0,0,0)", width=0, shape="hv"),
        fill="tozeroy", fillcolor="rgba(217, 83, 79, 0.13)",
        hoverinfo="skip", connectgaps=False
    ), secondary_y=False)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["outTemp"], name="室外気温",
    line=dict(color="rgba(200, 200, 200, 0.5)", width=1),
    fill="tozeroy", connectgaps=False
), secondary_y=False)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["roomTemp"], name="室内温度",
    line=dict(color="#00BFFF", width=3)
), secondary_y=False)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["acSetTemp"], name="設定温度",
    line=dict(color="#FF8C00", width=2, shape="hv", dash="dot")
), secondary_y=False)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["roomWBGT"], name="室内WBGT",
    line=dict(color="#E066FF", width=2)
), secondary_y=False)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["outWBGT"], name="室外WBGT",
    line=dict(color="rgba(224, 102, 255, 0.35)", width=1.5, dash="dot"),
    connectgaps=False
), secondary_y=False)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["roomHum"], name="室内湿度",
    line=dict(color="#32CD32", width=1.5, dash="dash")
), secondary_y=True)

fig.add_trace(go.Scatter(
    x=df_view["timestamp"], y=df_view["outHum"], name="室外湿度",
    line=dict(color="rgba(150, 200, 150, 0.4)", width=1, dash="dash"),
    connectgaps=False
), secondary_y=True)

fig.add_hline(y=28, line=dict(color="rgba(255, 140, 0, 0.4)", width=1, dash="dashdot"),
              annotation_text="WBGT 28 厳重警戒", annotation_position="top left")

fig.update_layout(
    height=600, margin=dict(l=20, r=20, t=40, b=20), hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    template="plotly_dark"
)
fig.update_yaxes(title_text="温度 / WBGT (℃)", secondary_y=False,
                 range=[TEMP_AXIS_MIN, TEMP_AXIS_MAX])
fig.update_yaxes(title_text="湿度 (%)", secondary_y=True, range=[20, 100], showgrid=False)

st.plotly_chart(fig, use_container_width=True)
st.caption("WBGT: 日本生気象学会の屋内近似式（輻射熱を含まない推定値） / 赤い帯はコンプレッサー稼働区間")

# ============================================================
# 3. 天気・雨量
# ============================================================
fig_w = go.Figure()
fig_w.add_trace(go.Bar(
    x=df_view["timestamp"], y=df_view["outPrecip"], name="雨量(mm)",
    marker=dict(color="#4A90D9"),
    customdata=df_view["weatherLabel"],
    hovertemplate="%{x}<br>雨量: %{y} mm<br>%{customdata}<extra></extra>"
))
fig_w.update_layout(
    height=200, margin=dict(l=20, r=20, t=30, b=20), hovermode="x unified",
    template="plotly_dark", showlegend=False,
    yaxis=dict(title_text="雨量 (mm)", rangemode="tozero")
)
st.plotly_chart(fig_w, use_container_width=True)

# ============================================================
# 4. 消費電力・電気代・コンプレッサー
# ============================================================
st.subheader("消費電力・電気代・コンプレッサー稼働")

if not has_power:
    st.info(
        "SwitchBotプラグミニの電力データが未取得です。"
        "GAS側の SWITCHBOT_DEVICE_ID を解決すると、このセクションに実測値が反映されます。"
    )
else:
    df_view["cumulative_cost_yen"] = df_view["cost_yen"].fillna(0).cumsum()

    last_24h = df[df["timestamp"] >= df["timestamp"].max() - pd.Timedelta(hours=24)]
    cost_24h = last_24h["cost_yen"].sum()
    kwh_24h = last_24h["kwh"].sum()
    duty_24h = last_24h["compressor"].mean() * 100 if last_24h["compressor"].notna().any() else np.nan
    duty_view = df_view["compressor"].mean() * 100 if df_view["compressor"].notna().any() else np.nan

    cur_watts = latest_valid(df["watts"])
    cur_comp = latest_valid(df["compressor"])

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("現在の消費電力", fmt(cur_watts, "W", digits=0),
              delta="コンプレッサー稼働中" if cur_comp == 1 else "送風/停止", delta_color="off")
    p2.metric("24時間の電気代", f"{cost_24h:.1f} 円", delta=f"{kwh_24h:.3f} kWh", delta_color="off")
    p3.metric("24時間の稼働率", fmt(duty_24h, "%", digits=0))
    p4.metric("表示期間の電気代", f"{df_view['cost_yen'].sum():.1f} 円",
              delta=f"稼働率 {duty_view:.0f} %" if pd.notna(duty_view) else None, delta_color="off")

    fig_p = make_subplots(rows=2, cols=1, shared_xaxes=True,
                          row_heights=[0.72, 0.28], vertical_spacing=0.06,
                          specs=[[{"secondary_y": True}], [{"secondary_y": False}]])

    fig_p.add_trace(go.Scatter(
        x=df_view["timestamp"], y=df_view["watts"], name="消費電力(W)",
        line=dict(color="#00BFFF", width=1.5), fill="tozeroy", connectgaps=False
    ), row=1, col=1, secondary_y=False)

    fig_p.add_trace(go.Scatter(
        x=df_view["timestamp"], y=df_view["cumulative_cost_yen"], name="累積電気代(円)",
        line=dict(color="#FF8C00", width=2, dash="dot")
    ), row=1, col=1, secondary_y=True)

    fig_p.add_trace(go.Scatter(
        x=df_view["timestamp"], y=df_view["compressor"], name="コンプレッサー",
        line=dict(color="#D9534F", width=1.5, shape="hv"),
        fill="tozeroy", connectgaps=False
    ), row=2, col=1)

    fig_p.update_layout(
        height=500, margin=dict(l=20, r=20, t=40, b=20), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_dark"
    )
    fig_p.update_yaxes(title_text="消費電力 (W)", rangemode="tozero", row=1, col=1, secondary_y=False)
    fig_p.update_yaxes(title_text="累積電気代 (円)", rangemode="tozero", showgrid=False,
                       row=1, col=1, secondary_y=True)
    fig_p.update_yaxes(title_text="稼働", range=[-0.1, 1.1], tickvals=[0, 1],
                       ticktext=["OFF", "ON"], row=2, col=1)

    st.plotly_chart(fig_p, use_container_width=True)
    st.caption(
        f"単価 {UNIT_PRICE:.2f} 円/kWh"
        f"（電力量料金 {KEPCO_TIER_RATE} + 燃料費調整 {KEPCO_FUEL_ADJ} + 再エネ賦課金 {KEPCO_RENEWABLE_LEVY}）"
        f" / コンプレッサー判定閾値 {COMPRESSOR_THRESHOLD_W} W"
    )

    daily = df.set_index("timestamp").resample("D").agg(
        電気代_円=("cost_yen", "sum"),
        電力量_kWh=("kwh", "sum"),
        稼働率_pct=("compressor", "mean"),
    ).dropna(how="all")
    daily["稼働率_pct"] = daily["稼働率_pct"] * 100

    if not daily.empty and daily["電気代_円"].sum() > 0:
        fig_d = go.Figure()
        fig_d.add_trace(go.Bar(x=daily.index, y=daily["電気代_円"], name="日別電気代(円)",
                               marker=dict(color="#FF8C00")))
        fig_d.update_layout(height=220, margin=dict(l=20, r=20, t=30, b=20),
                            template="plotly_dark", showlegend=False,
                            yaxis=dict(title_text="電気代 (円/日)", rangemode="tozero"))
        st.plotly_chart(fig_d, use_container_width=True)

with st.expander("読み込んだデータの確認"):
    st.write(f"総取得行数: {len(df)} / 期間: {df['timestamp'].min()} 〜 {df['timestamp'].max()}")
    st.write(f"watts保持行数: {int(df['watts'].notna().sum())}")
    st.write("表示期間内の天気の内訳:")
    st.dataframe(df_view["weatherLabel"].value_counts().rename("観測回数"), use_container_width=True)
    st.dataframe(df.tail(20), use_container_width=True)