import streamlit as st
import pandas as pd
import pydeck as pdk
import qrcode
from io import BytesIO
import requests
import math
import unicodedata
from datetime import datetime

# ============================================================
# GREEN-COLD
# Mô hình định tuyến động tối ưu chi phí và phát thải carbon
# cho chuỗi cung ứng lạnh nông sản xuất khẩu Việt Nam
#
# Phạm vi hành chính cấp tỉnh: 34 đơn vị hiện hành sau sắp xếp 2025.
# ============================================================

st.set_page_config(
    page_title="GREEN-COLD Logistics",
    layout="wide",
    page_icon="♻️"
)

# -----------------------------
# 1. PHẠM VI 34 TỈNH/THÀNH PHỐ
# -----------------------------
# Tọa độ đại diện dùng để định tuyến ở cấp tỉnh.
# Đây là điểm đại diện, không phải ranh giới polygon của tỉnh.
TINH_THANH = {
    "Hà Nội": (21.0285, 105.8542),
    "Cao Bằng": (22.6667, 106.2583),
    "Tuyên Quang": (21.8236, 105.2158),
    "Điện Biên": (21.3867, 103.0238),
    "Lai Châu": (22.4048, 103.4735),
    "Sơn La": (21.3274, 103.9048),
    "Lào Cai": (21.7167, 104.8667),
    "Thái Nguyên": (21.5942, 105.8481),
    "Lạng Sơn": (21.8523, 106.7581),
    "Quảng Ninh": (21.0061, 107.2925),
    "Bắc Ninh": (21.2731, 106.1947),
    "Phú Thọ": (21.3223, 105.4014),
    "Hải Phòng": (20.8449, 106.6881),
    "Hưng Yên": (20.6464, 106.0511),
    "Ninh Bình": (20.2546, 105.9754),
    "Thanh Hóa": (19.8067, 105.7852),
    "Nghệ An": (18.6796, 105.6813),
    "Hà Tĩnh": (18.3424, 105.9055),
    "Quảng Trị": (17.4723, 106.6025),
    "Huế": (16.4637, 107.5909),
    "Đà Nẵng": (16.0544, 108.2022),
    "Quảng Ngãi": (15.1214, 108.7923),
    "Gia Lai": (13.7829, 109.2190),
    "Khánh Hòa": (12.2388, 109.1967),
    "Đắk Lắk": (12.6667, 108.0382),
    "Lâm Đồng": (11.9404, 108.4583),
    "Đồng Nai": (10.9574, 106.8427),
    "Thành phố Hồ Chí Minh": (10.8231, 106.6297),
    "Tây Ninh": (10.5350, 106.4111),
    "Vĩnh Long": (10.2542, 105.9722),
    "Đồng Tháp": (10.3601, 106.3639),
    "Cà Mau": (9.1760, 105.1524),
    "An Giang": (10.0121, 105.0809),
    "Cần Thơ": (10.0452, 105.7469),
}

TINH_THANH_LIST = list(TINH_THANH.keys())

# -----------------------------
# 2. THAM SỐ MÔ HÌNH
# -----------------------------
# Đây là tham số mô hình để demo/kiểm thử.
# Khi có dữ liệu doanh nghiệp thực tế, thay bằng dữ liệu thực đo.
VEHICLE_CONFIG = {
    "Xe container lạnh 40 feet": {
        "fuel_l_per_100km": 28.0,
        "capacity_ton": 20.0,
    },
    "Xe tải lạnh 8 tấn": {
        "fuel_l_per_100km": 18.0,
        "capacity_ton": 8.0,
    },
}

DIESEL_PRICE_VND_L = 20000.0
CO2_KG_PER_L_DIESEL = 2.68
DEFAULT_CARBON_WEIGHT = 0.50

# OSRM cung cấp tuyến đường theo mạng đường bộ.
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


# -----------------------------
# 3. HÀM HỖ TRỢ
# -----------------------------
@st.cache_data(show_spinner=False)
def get_province_coordinate(name):
    return list(TINH_THANH[name])


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


@st.cache_data(show_spinner=False, ttl=3600)
def get_osrm_routes(start_lon, start_lat, end_lon, end_lat):
    """
    Lấy tuyến đường theo mạng đường bộ.
    alternatives=true yêu cầu máy chủ trả thêm phương án thay thế nếu có.
    """
    url = f"{OSRM_URL}/{start_lon},{start_lat};{end_lon},{end_lat}"

    params = {
        "overview": "full",
        "geometries": "geojson",
        "alternatives": "true",
        "steps": "false",
    }

    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": "GREEN-COLD-VNYLT-2026"},
        timeout=12,
    )
    response.raise_for_status()

    data = response.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("Không nhận được tuyến đường từ máy chủ định tuyến.")

    return data["routes"][:3]


def estimate_route(route, vehicle, diesel_price):
    """
    Tính chi phí nhiên liệu và phát thải cho một phương án.
    Đây là mô hình ước tính, chưa phải kiểm kê phát thải được xác minh.
    """
    distance_km = route["distance"] / 1000
    drive_hours = route["duration"] / 3600

    cfg = VEHICLE_CONFIG[vehicle]

    fuel_l = distance_km * cfg["fuel_l_per_100km"] / 100
    fuel_cost = fuel_l * diesel_price
    co2_kg = fuel_l * CO2_KG_PER_L_DIESEL

    return {
        "distance_km": distance_km,
        "drive_hours": drive_hours,
        "fuel_l": fuel_l,
        "fuel_cost_vnd": fuel_cost,
        "co2_kg": co2_kg,
        "geometry": route["geometry"],
    }


def minmax_score(value, min_value, max_value):
    """Chuẩn hóa giá trị: càng thấp càng tốt."""
    if max_value == min_value:
        return 0.0
    return (value - min_value) / (max_value - min_value)


def select_balanced_route(route_results, carbon_weight):
    """
    Hàm mục tiêu đa tiêu chí:

    Score = (1 - w) * CostScore + w * CarbonScore

    w = 0: ưu tiên chi phí
    w = 1: ưu tiên phát thải
    """
    min_cost = min(x["fuel_cost_vnd"] for x in route_results)
    max_cost = max(x["fuel_cost_vnd"] for x in route_results)

    min_co2 = min(x["co2_kg"] for x in route_results)
    max_co2 = max(x["co2_kg"] for x in route_results)

    scored = []

    for item in route_results:
        item = item.copy()

        item["cost_score"] = minmax_score(
            item["fuel_cost_vnd"], min_cost, max_cost
        )
        item["carbon_score"] = minmax_score(
            item["co2_kg"], min_co2, max_co2
        )

        item["objective_score"] = (
            (1 - carbon_weight) * item["cost_score"]
            + carbon_weight * item["carbon_score"]
        )

        scored.append(item)

    return min(scored, key=lambda x: x["objective_score"]), scored


def format_vnd(value):
    return f"{value:,.0f} đ".replace(",", ".")


def format_hours(hours):
    h = int(hours)
    m = int(round((hours - h) * 60))

    if m == 60:
        h += 1
        m = 0

    return f"{h} giờ {m} phút"


def geometry_to_path(geometry):
    return geometry["coordinates"]


def create_qr_text(
    origin,
    destination,
    vehicle,
    selected,
    carbon_weight,
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return (
        "GREEN-COLD LOGISTICS DATA RECORD\n"
        "Purpose: Emission-data management support\n"
        f"Origin province/city: {origin}\n"
        f"Destination province/city: {destination}\n"
        f"Vehicle: {vehicle}\n"
        f"Distance_km: {selected['distance_km']:.1f}\n"
        f"Travel_time: {format_hours(selected['drive_hours'])}\n"
        f"Estimated_fuel_cost_VND: {selected['fuel_cost_vnd']:.0f}\n"
        f"Estimated_CO2_kg: {selected['co2_kg']:.2f}\n"
        f"Carbon_weight: {carbon_weight:.2f}\n"
        "Routing_engine: OSRM road network\n"
        "Emission_method: fuel-based model\n"
        f"Generated_at: {now}\n"
        "Note: This record supports traceability; "
        "it is not a CBAM certificate."
    )


# -----------------------------
# 4. GIAO DIỆN
# -----------------------------
st.title("♻️ GREEN-COLD")

st.subheader(
    "Mô hình định tuyến động tối ưu chi phí và phát thải carbon "
    "cho chuỗi cung ứng lạnh nông sản xuất khẩu Việt Nam"
)

st.caption(
    "Phạm vi dữ liệu hành chính: 34 tỉnh/thành phố cấp tỉnh hiện hành. "
    "Hệ thống dùng điểm đại diện cấp tỉnh để mô phỏng định tuyến."
)

st.markdown("---")


# -----------------------------
# 5. SIDEBAR
# -----------------------------
with st.sidebar:
    st.header("🚚 CẤU HÌNH HÀNH TRÌNH")

    diem_di = st.selectbox(
        "Tỉnh/thành phố điểm đi",
        TINH_THANH_LIST,
        index=TINH_THANH_LIST.index("Hà Nội"),
    )

    diem_den = st.selectbox(
        "Tỉnh/thành phố điểm đến",
        TINH_THANH_LIST,
        index=TINH_THANH_LIST.index("Cần Thơ"),
    )

    loai_xe = st.selectbox(
        "Loại phương tiện",
        list(VEHICLE_CONFIG.keys()),
    )

    carbon_weight = st.slider(
        "Mức ưu tiên giảm phát thải",
        min_value=0.0,
        max_value=1.0,
        value=DEFAULT_CARBON_WEIGHT,
        step=0.1,
        help=(
            "0 = ưu tiên chi phí; 1 = ưu tiên phát thải."
        ),
    )

    st.markdown("### Tham số mô hình")

    diesel_price = st.number_input(
        "Giá dầu diesel (đồng/lít)",
        min_value=10000.0,
        max_value=50000.0,
        value=DIESEL_PRICE_VND_L,
        step=500.0,
    )

if diem_di == diem_den:
    st.warning("Vui lòng chọn hai tỉnh/thành phố khác nhau.")
    st.stop()


# -----------------------------
# 6. TỌA ĐỘ ĐIỂM ĐI / ĐIỂM ĐẾN
# -----------------------------
start_loc = get_province_coordinate(diem_di)
end_loc = get_province_coordinate(diem_den)


# -----------------------------
# 7. ĐỊNH TUYẾN ĐỘNG
# -----------------------------
with st.spinner("🔄 Đang tìm tuyến đường theo mạng đường bộ..."):
    try:
        routes = get_osrm_routes(
            start_loc[1],
            start_loc[0],
            end_loc[1],
            end_loc[0],
        )

        route_results = [
            estimate_route(
                route,
                loai_xe,
                diesel_price,
            )
            for route in routes
        ]

        selected_route, scored_routes = select_balanced_route(
            route_results,
            carbon_weight,
        )

        routing_mode = "Mạng đường bộ OSRM"

    except Exception:
        # Fallback minh bạch: không giả vờ đây là tuyến đường thực tế.
        straight_km = haversine_km(
            start_loc[0],
            start_loc[1],
            end_loc[0],
            end_loc[1],
        )

        cfg = VEHICLE_CONFIG[loai_xe]

        fallback_km = straight_km * 1.15
        fallback_hours = fallback_km / 60

        fuel_l = fallback_km * cfg["fuel_l_per_100km"] / 100
        fuel_cost = fuel_l * diesel_price
        co2 = fuel_l * CO2_KG_PER_L_DIESEL

        selected_route = {
            "distance_km": fallback_km,
            "drive_hours": fallback_hours,
            "fuel_l": fuel_l,
            "fuel_cost_vnd": fuel_cost,
            "co2_kg": co2,
            "geometry": {
                "coordinates": [
                    [start_loc[1], start_loc[0]],
                    [end_loc[1], end_loc[0]],
                ]
            },
        }

        scored_routes = [selected_route]
        routing_mode = "Ước tính tọa độ (fallback)"


# -----------------------------
# 8. KẾT QUẢ CHÍNH
# -----------------------------
st.success(
    f"Đã xác định phương án: **{diem_di} → {diem_den}** | "
    f"Phương tiện: **{loai_xe}**"
)

if routing_mode == "Ước tính tọa độ (fallback)":
    st.warning(
        "Máy chủ định tuyến không phản hồi. "
        "Kết quả hiện tại chỉ là ước tính khoảng cách, "
        "không phải tuyến đường thực tế."
    )
else:
    st.caption(
        "Chế độ định tuyến: mạng đường bộ. "
        "Số phương án phụ thuộc dữ liệu tuyến thay thế do máy chủ cung cấp."
    )

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "Khoảng cách",
        f"{selected_route['distance_km']:.1f} km",
    )

with c2:
    st.metric(
        "Thời gian chạy",
        format_hours(selected_route["drive_hours"]),
    )

with c3:
    st.metric(
        "Chi phí nhiên liệu ước tính",
        format_vnd(selected_route["fuel_cost_vnd"]),
    )

with c4:
    st.metric(
        "Phát thải ước tính",
        f"{selected_route['co2_kg']:.2f} kg CO₂",
    )

st.markdown("---")


# -----------------------------
# 9. SO SÁNH PHƯƠNG ÁN
# -----------------------------
left_col, right_col = st.columns([1.15, 1])

with left_col:
    st.subheader("📊 So sánh các phương án định tuyến")

    rows = []

    for i, item in enumerate(scored_routes, start=1):
        rows.append(
            {
                "Phương án": f"Tuyến {i}",
                "Khoảng cách (km)": round(item["distance_km"], 1),
                "Thời gian": format_hours(item["drive_hours"]),
                "Chi phí nhiên liệu": format_vnd(
                    item["fuel_cost_vnd"]
                ),
                "Phát thải (kg CO₂)": round(
                    item["co2_kg"], 2
                ),
                "Điểm mục tiêu": round(
                    item.get("objective_score", 0), 4
                ),
            }
        )

    df_routes = pd.DataFrame(rows)

    st.dataframe(
        df_routes,
        use_container_width=True,
        hide_index=True,
    )

    st.info(
        "GREEN-COLD lựa chọn phương án có điểm mục tiêu thấp nhất, "
        "kết hợp chi phí nhiên liệu và phát thải CO₂ theo trọng số "
        f"hiện tại: {1-carbon_weight:.1f} chi phí / "
        f"{carbon_weight:.1f} phát thải."
    )

    st.subheader("🧮 Logic tính toán")

    st.markdown(
        """
        **Chi phí nhiên liệu ước tính**

        `Chi phí = Quãng đường × Mức tiêu hao nhiên liệu × Giá nhiên liệu`

        **Phát thải CO₂ ước tính**

        `CO₂ = Lượng nhiên liệu tiêu thụ × Hệ số phát thải`

        **Lựa chọn tuyến**

        Các phương án được chuẩn hóa về cùng thang điểm cho hai tiêu chí
        chi phí và phát thải, sau đó kết hợp theo trọng số người dùng chọn.
        """
    )


# -----------------------------
# 10. BẢN ĐỒ TUYẾN
# -----------------------------
with right_col:
    st.subheader("🗺️ Tuyến đường được lựa chọn")

    path_data = pd.DataFrame(
        [
            {
                "path": geometry_to_path(
                    selected_route["geometry"]
                ),
                "name": "GREEN-COLD route",
            }
        ]
    )

    point_data = pd.DataFrame(
        [
            {
                "name": diem_di,
                "lat": start_loc[0],
                "lon": start_loc[1],
                "type": "Điểm đi",
            },
            {
                "name": diem_den,
                "lat": end_loc[0],
                "lon": end_loc[1],
                "type": "Điểm đến",
            },
        ]
    )

    center_lat = (start_loc[0] + end_loc[0]) / 2
    center_lon = (start_loc[1] + end_loc[1]) / 2

    st.pydeck_chart(
        pdk.Deck(
            initial_view_state=pdk.ViewState(
                latitude=center_lat,
                longitude=center_lon,
                zoom=5.2,
                pitch=0,
            ),
            tooltip={
                "text": "{name} - {type}"
            },
            layers=[
                pdk.Layer(
                    "PathLayer",
                    data=path_data,
                    get_path="path",
                    get_width=6,
                    get_color=[39, 174, 96],
                    width_min_pixels=3,
                ),
                pdk.Layer(
                    "ScatterplotLayer",
                    data=point_data,
                    get_position="[lon, lat]",
                    get_radius=18000,
                    get_fill_color=[46, 125, 50, 220],
                    pickable=True,
                ),
            ],
        )
    )


# -----------------------------
# 11. HỒ SƠ DỮ LIỆU PHÁT THẢI / QR
# -----------------------------
st.markdown("---")

st.subheader("📜 Hồ sơ dữ liệu phát thải GREEN-COLD")

st.write(
    "Mã QR lưu thông tin của một lần mô phỏng tuyến đường, "
    "phục vụ truy xuất và quản trị dữ liệu phát thải. "
    "Nó không phải chứng nhận CBAM và không thay thế hoạt động "
    "xác minh độc lập."
)

if st.button("📥 Tạo mã QR dữ liệu chuyến vận tải"):
    qr_text = create_qr_text(
        diem_di,
        diem_den,
        loai_xe,
        selected_route,
        carbon_weight,
    )

    qr = qrcode.QRCode(
        version=None,
        box_size=8,
        border=4,
    )

    qr.add_data(qr_text)
    qr.make(fit=True)

    img_qr = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    buf = BytesIO()
    img_qr.save(buf, format="PNG")

    st.image(buf.getvalue(), width=280)

    st.download_button(
        "⬇️ Tải mã QR",
        data=buf.getvalue(),
        file_name="green_cold_emission_record.png",
        mime="image/png",
    )


# -----------------------------
# 12. PHẠM VI VÀ GIỚI HẠN
# -----------------------------
with st.expander("ℹ️ Phạm vi và giới hạn của mô hình"):
    st.markdown(
        """
        - Hệ thống hiện mô phỏng ở **cấp tỉnh/thành phố**, sử dụng một điểm
          đại diện cho mỗi đơn vị hành chính.
        - Định tuyến sử dụng mạng đường bộ thông qua OSRM khi kết nối thành công.
        - Chi phí hiện tại là **chi phí nhiên liệu ước tính**, chưa bao gồm
          phí cầu đường, nhân công, khấu hao, phí lạnh, phí cảng hoặc
          các chi phí logistics khác.
        - Phát thải hiện tại là **ước tính theo nhiên liệu tiêu thụ** và
          hệ số phát thải được khai báo trong mô hình; chưa phải số liệu
          kiểm kê được bên thứ ba xác minh.
        - Dữ liệu có thể làm nền cho quản trị/truy xuất dữ liệu phát thải,
          nhưng không tự xác nhận doanh nghiệp hoặc lô hàng "tuân thủ CBAM".
        - Khi triển khai thực tế cần bổ sung GPS, giao thông, tải trọng,
          loại nhiên liệu, mức tiêu hao thực tế, phí đường bộ và dữ liệu
          phát thải được chuẩn hóa/xác minh.
        """
    )

st.caption("GREEN-COLD | Prototype phục vụ nghiên cứu VNYLT 2026")
