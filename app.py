import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime, date
import copy

# --- MongoDB Setup ---
client = MongoClient(st.secrets["mongo_uri"])
db = client["seamaster"]
collection = db["shipments"]

# --- Page Config and Styling ---
st.set_page_config(layout="wide")

st.markdown(
    """
    <style>
        .block-container {
            padding: 2rem 3rem;
        }
        .stDataFrame, .stDataEditor {
            font-size: 16px;
        }
        thead tr th {
            font-weight: bold;
            font-size: 16px;
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("🚛 Edit Shipment by Unique ID")
st.markdown("Search and edit truck shipment data by providing a unique shipment ID.")

# --- Input Unique ID ---
unique_id = st.text_input("🔎 Enter Shipment Unique ID")

if unique_id:
    shipment = collection.find_one({"Unique ID": unique_id})

    if not shipment:
        st.warning(f"No shipment found with Unique ID: {unique_id}")
    else:
        trucks_data = shipment.get("Trucks", [])

        def extract_ordered_keys(data_list, field):
            for item in data_list:
                if isinstance(item.get(field), dict):
                    return list(item[field].keys())
            return []

        border_keys = extract_ordered_keys(trucks_data, "Borders")
        trailer_keys = extract_ordered_keys(trucks_data, "Trailers")

        desired_columns = [
            "Truck Number", "Horse Number"
        ] + trailer_keys + [
            "Driver Name", "Passport NO.", "Contact NO.",
            "Tonnage", "ETA", "Status", "Cargo Description",
            "Current Location", "Load Location", "Destination",
            "Arrived at Loading point", "Loaded Date", "Dispatch date"
        ] + border_keys + [
            "Date Arrived", "Date offloaded", "Cancel", "Flag", "Comment"
        ]

        flattened_trucks = []
        for truck in trucks_data:
            flat_truck = {}
            for col in desired_columns:
                if col in trailer_keys:
                    flat_truck[col] = truck.get("Trailers", {}).get(col, "")
                elif col in border_keys:
                    flat_truck[col] = truck.get("Borders", {}).get(col, None)
                else:
                    flat_truck[col] = truck.get(col, None)
            flattened_trucks.append(flat_truck)

        trucks_df = pd.DataFrame(flattened_trucks)

        for col in trucks_df.columns:
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]):
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce").dt.date

        for col in desired_columns:
            if col not in trucks_df.columns:
                trucks_df[col] = None

        trucks_df = trucks_df[desired_columns]

        for col in ["Cancel", "Flag"]:
            if col in trucks_df.columns:
                trucks_df[col] = trucks_df[col].apply(lambda x: True if x else False)

        original_trucks_df = trucks_df.copy()

        # Column Configs
        column_config = {
            col: st.column_config.DateColumn(label=col, format="YYYY-MM-DD")
            for col in trucks_df.columns
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]) or col in border_keys
        }

        st.markdown("### 📝 Truck Details", help="Editable fields below")
        edited_trucks_df = st.data_editor(
            trucks_df,
            use_container_width=True,
            num_rows="dynamic",
            column_config=column_config,
            key="truck_editor"
        )

        if not original_trucks_df.equals(edited_trucks_df):
            st.warning("You have unsaved changes. Don't forget to click '📎 Save Changes'!", icon="⚠️")

        if "Status" in trucks_df.columns:
            status_summary = trucks_df["Status"].value_counts()
            if not status_summary.empty:
                st.markdown("### 📊 Truck Status Summary:")
                for label, count in status_summary.items():
                    st.markdown(f"- **{count} truck(s)** — {label}")
        else:
            st.info("No 'Status' column found to summarize.")

        st.markdown("### 🚚 Truck Summary")
        total_trucks = len(trucks_df)
        total_cancelled = trucks_df[trucks_df["Cancel"] == True].shape[0]
        total_on_route = trucks_df[trucks_df["Status"] == "On Route"].shape[0]
        total_at_destination = trucks_df[trucks_df["Status"] == "At Destination"].shape[0]

        st.markdown(f"- **Total Trucks**: {total_trucks}")
        st.markdown(f"- **Cancelled Trucks**: {total_cancelled}")

        st.divider()

        if st.button("📎 Save Changes"):
            try:
                updated_trucks = []

                for i, edited_row in enumerate(edited_trucks_df.to_dict(orient="records")):
                    if all((val == "" or val == 0 or pd.isna(val)) for val in edited_row.values()):
                        continue

                    trailers_data = {key: edited_row.pop(key) for key in trailer_keys if key in edited_row}
                    borders_data = {
                        key: datetime.combine(edited_row.pop(key), datetime.min.time()) if isinstance(edited_row[key], date)
                        else None
                        for key in border_keys if key in edited_row
                    }

                    cleaned_row = {}
                    for key, val in edited_row.items():
                        if pd.isna(val):
                            cleaned_row[key] = None
                        elif isinstance(val, date):
                            cleaned_row[key] = datetime.combine(val, datetime.min.time())
                        else:
                            cleaned_row[key] = val

                    cleaned_row["Trailers"] = trailers_data
                    cleaned_row["Borders"] = borders_data

                    if i < len(trucks_data):
                        original = copy.deepcopy(trucks_data[i])
                        original.update(cleaned_row)
                        updated_trucks.append(original)
                    else:
                        base_schema = copy.deepcopy(trucks_data[0]) if trucks_data else {}
                        new_truck = {
                            key: (
                                0 if isinstance(value, (int, float))
                                else None if isinstance(value, datetime)
                                else ""
                            )
                            for key, value in base_schema.items()
                        }
                        new_truck.update(cleaned_row)
                        updated_trucks.append(new_truck)

                result = collection.update_one(
                    {"Unique ID": unique_id},
                    {"$set": {"Trucks": updated_trucks}}
                )

                if result.modified_count == 1:
                    st.success("✅ Shipment updated successfully.")
                else:
                    st.info("ℹ️ No changes were made.")

            except Exception as e:
                st.error(f"❌ Failed to update shipment: {e}")
