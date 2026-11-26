import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

st.set_page_config(
    page_title="Fleet Management Dashboard",
    page_icon="🚚",
    layout="wide"
)

st.title("🚚 Fleet Fuel Efficiency & Maintenance Dashboard")
st.caption("Monitor vehicle performance, fuel expenses, routes, maintenance, and data quality.")

@st.cache_resource
def get_engine():
    database_url = st.secrets.get("DATABASE_URL")
    if not database_url:
        st.error("DATABASE_URL is missing. Add it in Streamlit Cloud → App settings → Secrets.")
        st.stop()

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    return create_engine(database_url, pool_pre_ping=True)

@st.cache_data(ttl=60)
def load_table(table_name):
    allowed_tables = {
        "vehicles", "trips", "fuel_transactions",
        "maintenance_records", "cleaned_trips", "data_quality_issues"
    }
    if table_name not in allowed_tables:
        raise ValueError("Invalid table name")
    with get_engine().connect() as connection:
        return pd.read_sql(text(f'SELECT * FROM "{table_name}"'), connection)

try:
    vehicles = load_table("vehicles")
    trips = load_table("trips")
    fuel = load_table("fuel_transactions")
    maintenance = load_table("maintenance_records")
    cleaned = load_table("cleaned_trips")
    issues = load_table("data_quality_issues")
except Exception as error:
    st.error(f"Could not load data from Neon PostgreSQL: {error}")
    st.info("Check that your Neon database contains the tables created by the Colab backend, and that DATABASE_URL is set in Streamlit Secrets.")
    st.stop()

# Prepare data types
for column in ["distance_km", "fuel_litres", "fuel_cost", "km_per_litre", "fuel_cost_per_km"]:
    if column in cleaned.columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

if "fuel_date" in fuel.columns:
    fuel["fuel_date"] = pd.to_datetime(fuel["fuel_date"], errors="coerce")
if "maintenance_date" in maintenance.columns:
    maintenance["maintenance_date"] = pd.to_datetime(maintenance["maintenance_date"], errors="coerce")

# Exclude duplicate receipts from fuel summaries
if "receipt_number" in fuel.columns:
    valid_fuel = fuel.drop_duplicates(subset=["receipt_number"], keep="first").copy()
else:
    valid_fuel = fuel.copy()

cleaned_vehicle = cleaned.merge(
    vehicles[["vehicle_id", "vehicle_type", "registration_number"]],
    on="vehicle_id", how="left"
) if not cleaned.empty else cleaned.copy()

monthly_cost = pd.DataFrame()
if not cleaned.empty and not valid_fuel.empty and "fuel_date" in valid_fuel.columns:
    date_lookup = valid_fuel[["trip_id", "vehicle_id", "fuel_date"]].drop_duplicates()
    monthly_data = cleaned.merge(date_lookup, on=["trip_id", "vehicle_id"], how="left")
    monthly_data["month"] = monthly_data["fuel_date"].dt.to_period("M").astype(str)
    monthly_cost = monthly_data.groupby("month", as_index=False).agg(
        fuel_cost=("fuel_cost", "sum"),
        distance_km=("distance_km", "sum")
    )
    if not maintenance.empty and "maintenance_date" in maintenance.columns:
        maintenance_month = maintenance.dropna(subset=["maintenance_date"]).copy()
        maintenance_month["month"] = maintenance_month["maintenance_date"].dt.to_period("M").astype(str)
        maintenance_month = maintenance_month.groupby("month", as_index=False)["maintenance_cost"].sum()
        monthly_cost = monthly_cost.merge(maintenance_month, on="month", how="left")
    else:
        monthly_cost["maintenance_cost"] = 0
    monthly_cost["maintenance_cost"] = monthly_cost["maintenance_cost"].fillna(0)
    monthly_cost["total_operating_cost"] = monthly_cost["fuel_cost"] + monthly_cost["maintenance_cost"]

# Sidebar navigation
st.sidebar.title("Fleet Management")
page = st.sidebar.radio(
    "Navigate",
    [
        "Dashboard Overview",
        "Fuel Analytics",
        "Vehicle Comparison",
        "Route Analysis",
        "Maintenance Analysis",
        "Data Quality",
        "Reports"
    ]
)
if st.sidebar.button("Refresh Dashboard"):
    st.cache_data.clear()
    st.rerun()

# Helpers
def money(value):
    return f"₹{value:,.0f}"

def show_empty(message):
    st.info(message)

if page == "Dashboard Overview":
    st.subheader("Dashboard Overview")
    total_vehicles = vehicles["vehicle_id"].nunique() if not vehicles.empty else 0
    total_distance = cleaned["distance_km"].sum() if not cleaned.empty else 0
    total_fuel = cleaned["fuel_litres"].sum() if not cleaned.empty else 0
    total_fuel_cost = cleaned["fuel_cost"].sum() if not cleaned.empty else 0
    avg_efficiency = cleaned["km_per_litre"].mean() if not cleaned.empty else 0
    total_maintenance = maintenance["maintenance_cost"].sum() if not maintenance.empty else 0
    issue_count = len(issues)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Registered Vehicles", f"{total_vehicles}")
    k2.metric("Total Distance", f"{total_distance:,.0f} km")
    k3.metric("Average Fuel Efficiency", f"{avg_efficiency:.2f} km/L")
    k4.metric("Fuel Cost", money(total_fuel_cost))

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("Fuel Consumed", f"{total_fuel:,.1f} L")
    k6.metric("Fuel Cost per km", money(total_fuel_cost / total_distance) if total_distance else "₹0")
    k7.metric("Maintenance Cost", money(total_maintenance))
    k8.metric("Data Quality Issues", f"{issue_count}")

    left, right = st.columns(2)
    with left:
        st.markdown("#### Fuel efficiency by vehicle")
        if not cleaned_vehicle.empty and "km_per_litre" in cleaned_vehicle.columns:
            vehicle_eff = cleaned_vehicle.groupby(
                ["vehicle_id", "vehicle_type"], as_index=False
            )["km_per_litre"].mean()
            fig = px.bar(vehicle_eff, x="vehicle_id", y="km_per_litre", color="vehicle_type",
                         labels={"vehicle_id": "Vehicle", "km_per_litre": "km per litre"})
            st.plotly_chart(fig, use_container_width=True)
        else:
            show_empty("No cleaned trip data available.")

    with right:
        st.markdown("#### Fuel and maintenance expenses")
        expense = pd.DataFrame({
            "Expense": ["Fuel", "Maintenance"],
            "Cost (₹)": [total_fuel_cost, total_maintenance]
        })
        fig = px.pie(expense, names="Expense", values="Cost (₹)", hole=0.45)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### Monthly operating expenses")
    if not monthly_cost.empty:
        fig = px.bar(monthly_cost, x="month",
                     y=["fuel_cost", "maintenance_cost"],
                     barmode="group",
                     labels={"value": "Cost (₹)", "month": "Month", "variable": "Expense"})
        st.plotly_chart(fig, use_container_width=True)
    else:
        show_empty("Monthly expense data is not available yet.")

elif page == "Fuel Analytics":
    st.subheader("Fuel Analytics")
    if cleaned.empty:
        show_empty("No cleaned trip data is available.")
    else:
        a, b, c = st.columns(3)
        a.metric("Fuel Used", f'{cleaned["fuel_litres"].sum():,.1f} L')
        b.metric("Average Efficiency", f'{cleaned["km_per_litre"].mean():.2f} km/L')
        c.metric("Fuel Cost / km", money(cleaned["fuel_cost"].sum() / cleaned["distance_km"].sum())
                 if cleaned["distance_km"].sum() else "₹0")

        if not cleaned_vehicle.empty:
            fuel_by_vehicle = cleaned_vehicle.groupby("vehicle_id", as_index=False).agg(
                fuel_litres=("fuel_litres", "sum"),
                fuel_cost=("fuel_cost", "sum")
            )
            fig = px.bar(fuel_by_vehicle, x="vehicle_id", y="fuel_litres",
                         labels={"vehicle_id": "Vehicle", "fuel_litres": "Fuel (litres)"})
            st.plotly_chart(fig, use_container_width=True)
        st.markdown("#### Cleaned fuel and trip records")
        st.dataframe(cleaned_vehicle, use_container_width=True)

    st.markdown("#### Original fuel transactions")
    st.dataframe(valid_fuel, use_container_width=True)

elif page == "Vehicle Comparison":
    st.subheader("Vehicle Comparison")
    if cleaned_vehicle.empty:
        show_empty("No vehicle performance data is available.")
    else:
        types = sorted(cleaned_vehicle["vehicle_type"].dropna().unique().tolist())
        selected_type = st.selectbox("Choose vehicle type", types)
        comparison = cleaned_vehicle[cleaned_vehicle["vehicle_type"] == selected_type]
        comparison = comparison.groupby(
            ["vehicle_id", "vehicle_type"], as_index=False
        ).agg(
            average_km_per_litre=("km_per_litre", "mean"),
            total_distance_km=("distance_km", "sum"),
            total_fuel_cost=("fuel_cost", "sum")
        ).sort_values("average_km_per_litre", ascending=False)
        fig = px.bar(comparison, x="vehicle_id", y="average_km_per_litre",
                     color="vehicle_type",
                     labels={"vehicle_id": "Vehicle", "average_km_per_litre": "Average km/L"})
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(comparison, use_container_width=True)

elif page == "Route Analysis":
    st.subheader("Route Analysis")
    if cleaned.empty or "route_name" not in cleaned.columns:
        show_empty("No route analysis data is available.")
    else:
        route_data = cleaned.groupby("route_name", as_index=False).agg(
            distance_km=("distance_km", "sum"),
            fuel_litres=("fuel_litres", "sum"),
            fuel_cost=("fuel_cost", "sum"),
            average_km_per_litre=("km_per_litre", "mean")
        )
        route_data["fuel_cost_per_km"] = route_data["fuel_cost"] / route_data["distance_km"].replace(0, pd.NA)
        left, right = st.columns(2)
        with left:
            fig = px.bar(route_data, x="route_name", y="average_km_per_litre",
                         labels={"route_name": "Route", "average_km_per_litre": "Average km/L"})
            st.plotly_chart(fig, use_container_width=True)
        with right:
            fig = px.bar(route_data, x="route_name", y="fuel_cost_per_km",
                         labels={"route_name": "Route", "fuel_cost_per_km": "Fuel cost per km (₹)"})
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(route_data, use_container_width=True)

elif page == "Maintenance Analysis":
    st.subheader("Maintenance Analysis")
    if maintenance.empty:
        show_empty("No maintenance records are available.")
    else:
        total = maintenance["maintenance_cost"].sum()
        st.metric("Total Maintenance Cost", money(total))
        by_vehicle = maintenance.groupby("vehicle_id", as_index=False)["maintenance_cost"].sum()
        fig = px.bar(by_vehicle, x="vehicle_id", y="maintenance_cost",
                     labels={"vehicle_id": "Vehicle", "maintenance_cost": "Maintenance cost (₹)"})
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("#### Maintenance records")
        st.dataframe(maintenance.sort_values("maintenance_date", ascending=False),
                     use_container_width=True)

elif page == "Data Quality":
    st.subheader("Data Quality & Verification")
    if issues.empty:
        show_empty("No data quality issues were recorded.")
    else:
        st.metric("Total Issues", len(issues))
        if "resolution_status" in issues.columns:
            pending = issues["resolution_status"].astype(str).str.lower().isin(
                ["pending", "open", "unresolved"]
            ).sum()
            st.metric("Pending Issues", int(pending))
        if "issue_type" in issues.columns:
            issue_summary = issues["issue_type"].value_counts().reset_index()
            issue_summary.columns = ["Issue Type", "Count"]
            fig = px.bar(issue_summary, x="Issue Type", y="Count")
            st.plotly_chart(fig, use_container_width=True)
            selected_issue = st.selectbox("Filter by issue type", ["All"] + issue_summary["Issue Type"].tolist())
            filtered = issues if selected_issue == "All" else issues[issues["issue_type"] == selected_issue]
        else:
            filtered = issues
        st.dataframe(filtered, use_container_width=True)
        st.download_button(
            "Download data quality records",
            data=filtered.to_csv(index=False).encode("utf-8"),
            file_name="data_quality_issues.csv",
            mime="text/csv"
        )

elif page == "Reports":
    st.subheader("Download Reports")
    reports = {
        "Cleaned Trip Dataset": cleaned,
        "Vehicle Registry": vehicles,
        "Trip Records": trips,
        "Fuel Transactions": fuel,
        "Maintenance Records": maintenance,
        "Data Quality Issues": issues
    }
    for title, dataframe in reports.items():
        st.markdown(f"**{title}**")
        st.download_button(
            f"Download {title}",
            data=dataframe.to_csv(index=False).encode("utf-8"),
            file_name=title.lower().replace(" ", "_") + ".csv",
            mime="text/csv",
            key=title
        )
