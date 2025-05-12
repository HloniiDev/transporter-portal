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

# --- Input Unique ID ---
unique_id = st.text_input("🔎 Enter Shipment Unique ID")

if unique_id:
    shipment = collection.find_one({"Unique ID": unique_id})

    if not shipment:
        st.warning(f"No shipment found with Unique ID: {unique_id}")
    else:
        st.success("Shipment found. Edit the truck data below.")

        trucks_data = shipment.get("Trucks", [])

        # Step 1: Your required columns (in order)
        desired_columns = [
            "Truck Number", "Horse Number", "Trailer Number", "Driver Name", "Passport NO.", "Contact NO.",
            "Tonnage", "ETA", "Status", "Current Location", "Load Location", "Destination",
            "Arrived at Loading point", "Loaded Date", "Dispatch date",
            "Date Arrived", "Date offloaded", "Cancel", "Flag", "Comment"
        ]

        # Step 2: Normalize each truck dict
        for truck in trucks_data:
            for col in desired_columns:
                truck.setdefault(col, None)

        # Step 3: Convert list or dict fields to string if needed
        flattened_trucks = []
        for truck in trucks_data:
            flat_truck = truck.copy()
            for key in flat_truck:
                val = flat_truck.get(key)
                if isinstance(val, list):
                    flat_truck[key] = ", ".join(map(str, val))
                elif isinstance(val, dict):
                    flat_truck[key] = str(val)
            flattened_trucks.append(flat_truck)

        trucks_df = pd.DataFrame(flattened_trucks)

        # Step 4: Convert relevant fields to datetime
        for col in trucks_df.columns:
            if any(x in col.lower() for x in ["date", "dispatch", "arrival", "eta"]):
                trucks_df[col] = pd.to_datetime(trucks_df[col], errors="coerce")

        # Step 5: Ensure all required columns exist (again)
        for col in desired_columns:
            if col not in trucks_df.columns:
                trucks_df[col] = None

        # Step 6: Reorder columns strictly
        trucks_df = trucks_df[desired_columns]

        # --- Modify 'Cancel' and 'Flag' columns to be checkboxes ---
        for col in ["Cancel", "Flag"]:
            if col in trucks_df.columns:
                trucks_df[col] = trucks_df[col].apply(lambda x: True if x else False)

        # Step 7: Editable Table with checkboxes for 'Cancel' and 'Flag'
        st.markdown("### 📝 Truck Details")
        edited_trucks_df = st.data_editor(
            trucks_df,
            use_container_width=True,
            num_rows="dynamic",
            key="truck_editor"
        )

        # --- Truck Status Summary (based on "Status") ---
        if "Status" in trucks_df.columns:
            status_summary = trucks_df["Status"].value_counts()
            if not status_summary.empty:
                st.markdown("### 📊 Truck Status Summary:")
                for label, count in status_summary.items():
                    st.markdown(f"- **{count} truck(s)** — {label}")
        else:
            st.info("No 'Status' column found to summarize.")

        # --- Truck Summary Section ---
        st.markdown("### 🚚 Truck Summary")
        st.write("Here you can summarize the details of the trucks listed in the table.")

        # Example of summary content, adjust as necessary
        total_trucks = len(trucks_df)
        total_cancelled = trucks_df[trucks_df["Cancel"] == True].shape[0]
        total_on_route = trucks_df[trucks_df["Status"] == "On Route"].shape[0]
        total_at_destination = trucks_df[trucks_df["Status"] == "At Destination"].shape[0]

        st.markdown(f"- **Total Trucks**: {total_trucks}")
        st.markdown(f"- **Cancelled Trucks**: {total_cancelled}")
        # st.markdown(f"- **On Route Trucks**: {total_on_route}")
        # st.markdown(f"- **At Destination Trucks**: {total_at_destination}")

        st.divider()

        # Step 9: Save Changes
        if st.button("💾 Save Changes"):
            try:
                updated_trucks = []

                for i, edited_row in enumerate(edited_trucks_df.to_dict(orient="records")):
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

                # Update MongoDB
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