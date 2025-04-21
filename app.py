import streamlit as st
from pymongo import MongoClient
import pandas as pd
from datetime import datetime
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

# --- Step 1: Input Unique ID ---
unique_id = st.text_input("🔎 Enter Shipment Unique ID")

if unique_id:
    shipment = collection.find_one({"Unique ID": unique_id})

    if not shipment:
        st.warning(f"No shipment found with Unique ID: {unique_id}")
    else:
        st.success("Shipment found. Edit the truck data below.")

        trucks_data = shipment.get("Trucks", [])

        # --- Step 2: Normalize Truck Schema ---
        all_keys = set()
        for truck in trucks_data:
            all_keys.update(truck.keys())

        for truck in trucks_data:
            for key in all_keys:
                truck.setdefault(key, None)

        # --- Step 3: Flatten fields like Trailers or Trailer Type for editing ---
        flattened_trucks = []
        for truck in trucks_data:
            flat_truck = truck.copy()
            for key in ["Trailers", "Trailer Type"]:
                val = flat_truck.get(key)
                if isinstance(val, list):
                    flat_truck[key] = ", ".join(map(str, val))  # list to comma-separated string
                elif isinstance(val, dict):
                    flat_truck[key] = str(val)
            flattened_trucks.append(flat_truck)

        trucks_df = pd.DataFrame(flattened_trucks)

        # Convert date-like strings to datetime
        for col in trucks_df.columns:
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]):
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce")

        # --- Step 4: Show editable DataFrame ---
        st.markdown("### 📝 Truck Details")
        edited_trucks_df = st.data_editor(
            trucks_df,
            use_container_width=True,
            num_rows="dynamic",
            key="truck_editor"
        )

        st.divider()

        # --- Step 5: Save Changes ---
        if st.button("💾 Save Changes"):
            try:
                updated_trucks = []

                for i, edited_row in enumerate(edited_trucks_df.to_dict(orient="records")):
                    # Skip if row is empty
                    if all((val == "" or val == 0 or pd.isna(val)) for val in edited_row.values()):
                        continue

                    cleaned_row = {}
                    for key, val in edited_row.items():
                        if pd.isna(val):
                            cleaned_row[key] = None
                        elif isinstance(val, pd.Timestamp):
                            cleaned_row[key] = val.to_pydatetime()
                        else:
                            cleaned_row[key] = val

                    # Convert fields back to lists
                    for list_key in ["Trailers", "Trailer Type"]:
                        if list_key in cleaned_row and isinstance(cleaned_row[list_key], str):
                            cleaned_row[list_key] = [
                                item.strip() for item in cleaned_row[list_key].split(",") if item.strip()
                            ]

                    if i < len(trucks_data):
                        # Update original truck object
                        original = copy.deepcopy(trucks_data[i])
                        original.update(cleaned_row)
                        updated_trucks.append(original)
                    else:
                        # Add new truck
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

                # --- Update MongoDB ---
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
