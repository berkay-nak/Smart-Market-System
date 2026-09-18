import streamlit as st
import sqlite3
import pandas as pd
import time


st.set_page_config(page_title="Smart Market Dashboard", layout="wide")



def load_data(query):
    conn = sqlite3.connect("smart_market.db")
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


st.title("🛒 Akıllı Market - Gerçek Zamanlı Stok Paneli")


df_products = load_data("SELECT * FROM Products")
df_transactions = load_data("SELECT * FROM Transactions ORDER BY timestamp DESC LIMIT 10")  # Son 10 işlemi getir


col1, col2, col3 = st.columns(3)
col1.metric(label="Toplam Ürün Çeşidi", value=len(df_products))
col2.metric(label="Gerçekleşen İşlem Sayısı", value=len(df_transactions))

# Stok 5'in altına düşenleri bulup uyarı verelim
kritik_stok = len(df_products[df_products['current_stock'] < 5])
col3.metric(label="Kritik Stok Uyarısı", value=kritik_stok, delta="-Acil Yenile!" if kritik_stok > 0 else "Normal",
            delta_color="inverse")

st.markdown("---")


sol_sutun, sag_sutun = st.columns(2)

with sol_sutun:
    st.subheader("📦 Anlık Raf Durumu")

    st.dataframe(df_products[['shelf_name', 'product_name', 'current_stock', 'max_stock']], use_container_width=True)

    st.subheader("📊 Stok Seviyesi Grafiği")

    st.bar_chart(data=df_products.set_index('product_name')['current_stock'])

with sag_sutun:
    st.subheader("⏱️ Son Alışveriş İşlemleri (Canlı Akış)")

    st.dataframe(df_transactions[['timestamp', 'customer_id', 'shelf_name', 'action_type', 'quantity']],
                 use_container_width=True)
    time.sleep(2)
    st.rerun()