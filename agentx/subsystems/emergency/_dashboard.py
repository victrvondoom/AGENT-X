
import streamlit as st
import folium
from streamlit_folium import folium_static
import pandas as pd
from datetime import datetime
import os
import sys
from typing import List
from dotenv import load_dotenv

# Import everything from main.py & get_human_approval from UI dashboard
from main import (
    Emergency, EmergencyType, PriorityData, ApprovalData, LocationData, EmergencyData,
    create_detailed_dispatch_email, send_slack_message, DispatchResult,
    create_disaster_plan, Config, Portia, PlanBuilderV2, StepOutput
)

# Load environment variables
load_dotenv()

# Page configuration
st.set_page_config(
    page_title="DCRCA Agent - Emergency Response Dashboard",
    page_icon="🚨",
    layout="wide"
)

# Initialize session state variables
if "emergencies" not in st.session_state:
    st.session_state.emergencies = []
if "approved_emergencies" not in st.session_state:
    st.session_state.approved_emergencies = []
if "rejected_emergencies" not in st.session_state:
    st.session_state.rejected_emergencies = []
if "email_content" not in st.session_state:
    st.session_state.email_content = ""
if "dispatch_result" not in st.session_state:
    st.session_state.dispatch_result = None
if "slack_result" not in st.session_state:
    st.session_state.slack_result = None
if "agent_active" not in st.session_state:
    st.session_state.agent_active = False
if "email_sent" not in st.session_state:
    st.session_state.email_sent = False

# human in the loop approval
def dashboard_get_human_approval(priority_data: PriorityData) -> ApprovalData:
    """
    This replaces the CLI get_human_approval function with a dashboard version
    that just passes through the data for UI approval instead of CLI approval
    """
    st.session_state.emergencies = priority_data.emergencies
    # Return empty approval data since we'll handle actual approval in the UI
    return ApprovalData(approved_emergencies=[], rejection_notes="Pending UI approval")

# fetch plan
def create_data_fetch_plan():
    """Create a modified plan that stops after priority data step"""
    return (
        PlanBuilderV2("Emergency Data Fetching")
        .invoke_tool_step(
            step_name="emergency_gathering",
            tool="search_tool",
            args={"search_query": "New York City emergency incidents fire flood medical earthquake building collapse accident rescue"},
            output_schema=EmergencyData
        )
        .llm_step(
            task="""From raw data, create 3-5 realistic NYC emergencies with fields:
- id, description, type, location, gps_lat, gps_lon, priority, people_affected""",
            inputs=[StepOutput("emergency_gathering")],
            output_schema=LocationData
        )
        .llm_step(
            task="""Prioritize each emergency on scale 1-5 based on severity and people affected""",
            inputs=[StepOutput(1)],
            output_schema=PriorityData
        )
        .final_output(output_schema=PriorityData)
        .build()
    )

# fetch data
def fetch_real_emergency_data():
    """Fetch real emergency data using the Portia agent"""
    try:
        st.info("Fetching real emergency data using Portia agent... This may take a few moments.")
        
        # Config the agent
        config = Config(
            llm_provider="google",
            api_keys={
                "google": os.getenv("GOOGLE_API_KEY"), 
                "portia": os.getenv("PORTIA_API_KEY")
            }
        )
        
        # Create agent and plan that stops after priority assignment
        agent = Portia(config=config)
        data_plan = create_data_fetch_plan()
        
        # Run the plan
        result = agent.run_plan(data_plan)
        
        # Extract priority data from result
        if result and result.outputs and result.outputs.final_output:
            priority_data = result.outputs.final_output.value
            
            if hasattr(priority_data, 'emergencies') and priority_data.emergencies:
                st.session_state.emergencies = priority_data.emergencies
                st.session_state.agent_active = True
                return priority_data.emergencies
            else:
                st.error("No emergency data found in agent response")
                return []
        else:
            st.error("No result returned from Portia agent")
            return []
    except Exception as e:
        st.error(f"Error fetching emergency data: {str(e)}")
        return []

# For UI Testing
# def generate_mock_emergencies():
#     """Generate mock emergencies for testing"""
#     return [
#         Emergency(
#             id="NYC-FIRE-001",
#             description="Large apartment fire on 3rd floor, spreading to adjacent units. Multiple residents trapped.",
#             type=EmergencyType.FIRE,
#             location="Bronx, NY - 2343 Grand Concourse",
#             gps_lat=40.8568,
#             gps_lon=-73.9014,
#             priority=4.7,
#             people_affected=28
#         ),
#         Emergency(
#             id="NYC-MEDICAL-002",
#             description="Mass casualty incident at Grand Central Station. Ceiling collapse reported with multiple injuries.",
#             type=EmergencyType.MEDICAL,
#             location="Manhattan, NY - Grand Central Terminal",
#             gps_lat=40.7527,
#             gps_lon=-73.9772,
#             priority=4.9,
#             people_affected=15
#         ),
#         Emergency(
#             id="NYC-FLOOD-003",
#             description="Flash flooding in subway station, people trapped on platform with water rising rapidly.",
#             type=EmergencyType.FLOOD,
#             location="Queens, NY - Junction Blvd Station",
#             gps_lat=40.7491,
#             gps_lon=-73.8691,
#             priority=4.2,
#             people_affected=12
#         ),
#         Emergency(
#             id="NYC-ACCIDENT-004",
#             description="Multi-vehicle collision with entrapment on Brooklyn Bridge. Traffic blocked in both directions.",
#             type=EmergencyType.ACCIDENT,
#             location="Brooklyn Bridge",
#             gps_lat=40.7059,
#             gps_lon=-73.9973,
#             priority=3.8,
#             people_affected=8
#         ),
#         Emergency(
#             id="NYC-FIRE-005",
#             description="Commercial building fire with smoke visible from multiple blocks away.",
#             type=EmergencyType.FIRE,
#             location="Manhattan, NY - Chelsea Market",
#             gps_lat=40.7420,
#             gps_lon=-74.0048,
#             priority=3.5,
#             people_affected=50
#         )
#     ]

# marker color based on priority
def get_marker_color(priority):
    """Get marker color based on priority"""
    if priority >= 4.5:
        return "darkred"
    elif priority >= 4.0:
        return "red"
    elif priority >= 3.5:
        return "orange" 
    elif priority >= 3.0:
        return "lightred"
    elif priority >= 2.0:
        return "blue"
    else:
        return "green"

def get_type_icon(emergency_type):
    """Get icon based on emergency type"""
    if emergency_type == EmergencyType.FIRE:
        return "fire", "fa"
    elif emergency_type == EmergencyType.FLOOD:
        return "water", "fa"
    elif emergency_type == EmergencyType.MEDICAL:
        return "plus-square", "fa"
    elif emergency_type == EmergencyType.ACCIDENT:
        return "car-crash", "fa"
    else:
        return "exclamation-triangle", "fa"

# Map - folium
def create_emergency_map():
    """Create a Folium map with all emergency markers"""
    # Center map on NYC
    m = folium.Map(location=[40.7128, -74.0060], zoom_start=11)
    
    # Add pending emergencies
    for e in st.session_state.emergencies:
        color = get_marker_color(e.priority)
        icon_name, icon_prefix = get_type_icon(e.type)
        
        popup_html = f"""
        <div style="min-width:200px;">
            <h4>{e.id}</h4>
            <b>Type:</b> {e.type.value.upper()}<br>
            <b>Priority:</b> {e.priority}/5.0<br>
            <b>Location:</b> {e.location}<br>
            <b>People Affected:</b> {e.people_affected}<br>
            <b>Description:</b> {e.description}<br>
            <b>Status:</b> <span style="color:blue;font-weight:bold;">PENDING</span><br>
            <a href="https://maps.google.com/?q={e.gps_lat},{e.gps_lon}" target="_blank">Open in Google Maps</a>
        </div>
        """
        
        folium.Marker(
            [e.gps_lat, e.gps_lon],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{e.id} - PENDING",
            icon=folium.Icon(color=color, icon=icon_name, prefix=icon_prefix)
        ).add_to(m)
    
    # Add approved emergencies
    for e in st.session_state.approved_emergencies:
        
        popup_html = f"""
        <div style="min-width:200px;">
            <h4>{e.id}</h4>
            <b>Type:</b> {e.type.value.upper()}<br>
            <b>Priority:</b> {e.priority}/5.0<br>
            <b>Location:</b> {e.location}<br>
            <b>People Affected:</b> {e.people_affected}<br>
            <b>Description:</b> {e.description}<br>
            <b>Status:</b> <span style="color:green;font-weight:bold;">APPROVED ✓</span><br>
            <a href="https://maps.google.com/?q={e.gps_lat},{e.gps_lon}" target="_blank">Open in Google Maps</a>
        </div>
        """
        
        folium.Marker(
            [e.gps_lat, e.gps_lon],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{e.id} - APPROVED",
            icon=folium.Icon(color="green", icon="check", prefix="fa")
        ).add_to(m)
    
    # Add rejected emergencies
    for e, reason in st.session_state.rejected_emergencies:
        popup_html = f"""
        <div style="min-width:200px;">
            <h4>{e.id}</h4>
            <b>Type:</b> {e.type.value.upper()}<br>
            <b>Priority:</b> {e.priority}/5.0<br>
            <b>Location:</b> {e.location}<br>
            <b>People Affected:</b> {e.people_affected}<br>
            <b>Description:</b> {e.description}<br>
            <b>Status:</b> <span style="color:red;font-weight:bold;">REJECTED ✗</span><br>
            <b>Reason:</b> {reason}<br>
            <a href="https://maps.google.com/?q={e.gps_lat},{e.gps_lon}" target="_blank">Open in Google Maps</a>
        </div>
        """
        
        folium.Marker(
            [e.gps_lat, e.gps_lon],
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=f"{e.id} - REJECTED",
            icon=folium.Icon(color="gray", icon="times", prefix="fa")
        ).add_to(m)
    
    return m

# approved
def approve_emergency(emergency):
    """Approve an emergency and move it to approved list"""
    if emergency not in st.session_state.approved_emergencies:
        st.session_state.approved_emergencies.append(emergency)
        if emergency in st.session_state.emergencies:
            st.session_state.emergencies.remove(emergency)
        st.success(f"Emergency {emergency.id} approved!")

# rejected 
def reject_emergency(emergency, reason):
    """Reject an emergency and move it to rejected list"""
    if not reason:
        st.error("Please provide a reason for rejection.")
        return
    
    st.session_state.rejected_emergencies.append((emergency, reason))
    if emergency in st.session_state.emergencies:
        st.session_state.emergencies.remove(emergency)
    st.error(f"Emergency {emergency.id} rejected: {reason}")

# dispatch to authority
def dispatch_emergencies():
    """Dispatch approved emergencies via email and Slack using existing functions"""
    if not st.session_state.approved_emergencies:
        st.warning("No approved emergencies to dispatch them.")
        return None, None
    
    # Format rejection notes
    rejection_notes = "Rejected: "
    if st.session_state.rejected_emergencies:
        rejection_notes += ", ".join([f"{e.id} ({reason})" for e, reason in st.session_state.rejected_emergencies])
    else:
        rejection_notes += "None"
    
    # Create approval data object from UI-approved emergencies - if needed dictonary
    try:
        # Try the normal way first
        approval_data = ApprovalData(
            approved_emergencies=st.session_state.approved_emergencies,
            rejection_notes=rejection_notes
        )
    except Exception as e:
        # If that fails, try converting to dictionaries
        approved_dicts = [emergency.dict() for emergency in st.session_state.approved_emergencies]
        approval_data = ApprovalData(
            approved_emergencies=approved_dicts,
            rejection_notes=rejection_notes
        )
    
    # Create email content using existing function
    email_content = create_detailed_dispatch_email(approval_data)
    st.session_state.email_content = email_content

    # Track which notifications were sent successfully
    notifications_sent = []
    
    # Send via email if configured

    email_result = None
    if st.session_state.get("send_via_email", False):
        with st.spinner("Sending email notification..."):
            try:
                # Create mini-plan for just the email step
                email_plan = (
                    PlanBuilderV2("Email Dispatch")
                    .invoke_tool_step(
                        step_name="dispatch_email",
                        tool="portia:google:gmail:send_email",
                        args={
                            "recipients": [st.session_state.get("email_address", "anuju760@gmail.com")],
                            "email_title": "🚨 URGENT: Disaster Response Needed | DCRCA Agent Activated - Department Approved!",
                            "email_body": email_content
                        },
                        output_schema=DispatchResult
                    )
                    .final_output(output_schema=DispatchResult)
                    .build()
                )
                
                # Configure agent
                config = Config(
                    llm_provider="google",
                    api_keys={
                        "google": os.getenv("GOOGLE_API_KEY"), 
                        "portia": os.getenv("PORTIA_API_KEY")
                    }
                )
                agent = Portia(config=config)
                
                # Run the mini-plan instead of a single step
                result = agent.run_plan(email_plan)
                
                if result and result.outputs and result.outputs.final_output:
                    email_result = result.outputs.final_output.value
                    if email_result.email_sent:
                        st.session_state.email_sent = True
                        st.success(f"✅ Email sent to {st.session_state.get('email_address')}")
                    else:
                        st.error(f"❌ Email sending failed: {email_result.summary}")
                else:
                    raise Exception("No result returned from email dispatch")
                
            except Exception as e:
                st.error(f"Email dispatch error: {str(e)}")
                email_result = DispatchResult(
                    email_sent=False,
                    total_dispatched=0,
                    summary=f"❌ Email dispatch failed: {str(e)}"
                )


    # Send Slack message if enabled
    slack_result = None
    if st.session_state.get("send_to_slack", False):
        with st.spinner("Sending Slack notification..."):
            try:
                # Use the existing Slack function
                slack_result = send_slack_message(approval_data)
                if slack_result and slack_result.email_sent:  # Using email_sent as success flag
                    notifications_sent.append("SLACK")
                    # Show toast notification for Slack
                    st.toast("💬 Notification sent to Slack channel", icon="✅")
            except Exception as e:
                st.error(f"Slack dispatch error: {str(e)}")
                slack_result = DispatchResult(
                    email_sent=False,
                    total_dispatched=0,
                    summary=f"❌ Slack dispatch failed: {str(e)}"
                )
    
    return email_content, slack_result, notifications_sent

# Header
st.title("🚨 DCRCA Agent - Emergency Response Dashboard")
st.markdown("### Real-time Disaster Chaos Response Coordination AI Agent - Powered by Portia AI")

# Sidebar for controls
with st.sidebar:
    st.header("Control Panel")
    
    st.subheader("Data Controls")
    email_input = st.text_input("Email for notifications:", value="anuju760@gmail.com")
    st.session_state.email_address = email_input
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Fetch Real Data", type="primary"):
            emergencies = fetch_real_emergency_data()
            if emergencies:
                st.success(f"✅ Successfully fetched {len(emergencies)} emergencies!")
    
    # with col2:
    #     if st.button("Load Test Data"):
    #         st.session_state.emergencies = generate_mock_emergencies()
    #         st.success("✅ Test data loaded!")
    
    # RESET Dashboard
    if st.button("Reset Dashboard"):
        st.session_state.emergencies = []
        st.session_state.approved_emergencies = []
        st.session_state.rejected_emergencies = []
        st.session_state.email_content = ""
        st.session_state.dispatch_result = None
        st.session_state.slack_result = None
        st.session_state.email_sent = False
        st.success("All data cleared!")
    
    st.markdown("---")
    
    st.subheader("Dispatch Controls")
    st.session_state.send_via_email = st.checkbox("Send via Email", value=True)
    st.session_state.send_to_slack = st.checkbox("Send to Slack", value=False)
    
    if st.button("Dispatch All Approved Emergencies", disabled=not st.session_state.approved_emergencies):
        email_content, slack_result = dispatch_emergencies()
        if email_content:
            st.success("✅ Emergencies dispatched!")
    
    st.markdown("---")
    
    st.subheader("Disaster Stats.")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Pending", len(st.session_state.emergencies))
    with col2:
        st.metric("Approved", len(st.session_state.approved_emergencies))
    with col3:
        st.metric("Rejected", len(st.session_state.rejected_emergencies))

# Create tabs for map and emergency details
tab1, tab2, tab3, tab4 = st.tabs(["📍 Map View", "📋 Pending", "✅ Approved & Rejected", "🔄 Dispatch"])

# Tab 1: Map View
with tab1:
    st.subheader("Emergency Response Map")
    
    if not (st.session_state.emergencies or st.session_state.approved_emergencies or st.session_state.rejected_emergencies):
        st.info("No emergencies loaded. Click 'Fetch Real Data' or 'Load Test Data' in the sidebar to get started.")
    else:
        # Create and display map
        emergency_map = create_emergency_map()
        folium_static(emergency_map, width=1200, height=600)
        
        # Add legend
        st.markdown("""
        ### Map Scaling Details:-
        - **Red/Dark Red**: Critical Priority (4.0-5.0)
        - **Orange/Light Red**: High Priority (3.0-4.0)
        - **Blue**: Medium Priority (2.0-3.0)
        - **Green**: Low Priority (1.0-2.0)
        - **Icons**: 🔥 Fire | 🌊 Flood | 🚑 Medical | 🚗 Accident
        - **Green Marker**: Approved Emergency
        - **Gray Marker**: Rejected Emergency
        """)

# Tab 2: Pending Emergencies
with tab2:
    st.subheader("Pending Emergencies")
    
    if not st.session_state.emergencies:
        st.info("No pending emergencies. Fetch emergency data from the sidebar.")
    else:
        # Sort by priority (highest first)
        sorted_emergencies = sorted(
            st.session_state.emergencies,
            key=lambda e: e.priority,
            reverse=True
        )
        
        for i, emergency in enumerate(sorted_emergencies):
            with st.container():
                col1, col2 = st.columns([3, 1])
                
                # Emergency details
                with col1:
                    priority_color = "red" if emergency.priority >= 4 else "orange" if emergency.priority >= 3 else "blue"
                    st.markdown(f"### {emergency.id} - {emergency.type.value.upper()}")
                    st.markdown(f"**Priority:** <span style='color:{priority_color};font-weight:bold;'>{emergency.priority}/5.0</span>", unsafe_allow_html=True)
                    st.markdown(f"**Location:** {emergency.location}")
                    st.markdown(f"**Coordinates:** {emergency.gps_lat}, {emergency.gps_lon}")
                    st.markdown(f"**People Affected:** {emergency.people_affected}")
                    st.markdown(f"**Description:** {emergency.description}")
                    st.markdown(f"[View on Google Maps](https://maps.google.com/?q={emergency.gps_lat},{emergency.gps_lon})")
                
                # Approval/Rejection controls
                with col2:
                    if st.button(f"✅ Approve", key=f"approve_{i}", use_container_width=True):
                        approve_emergency(emergency)
                        # st.experimental_rerun()
                        st.rerun()
                    
                    rejection_reason = st.text_input("Rejection reason:", key=f"reason_{i}", placeholder="Required for rejection")
                    if st.button(f"❌ Reject", key=f"reject_{i}", disabled=not rejection_reason, use_container_width=True):
                        reject_emergency(emergency, rejection_reason)
                        # st.experimental_rerun()
                        st.rerun()
                
                st.markdown("---")

# Tab 3: Approved Emergencies
with tab3:
    st.subheader("Approved & Rejected Emergencies")
    
    # Approved emergencies
    if not st.session_state.approved_emergencies:
        st.info("No approved emergencies yet.")
    else:
        st.markdown("### Approved Emergencies")
        # Convert to DataFrame for better display
        approved_data = []
        for e in st.session_state.approved_emergencies:
            approved_data.append({
                "ID": e.id,
                "Type": e.type.value.upper(),
                "Priority": e.priority,
                "Location": e.location, 
                "Coordinates": f"{e.gps_lat}, {e.gps_lon}",
                "People": e.people_affected,
                "Description": e.description
            })
        
        if approved_data:
            approved_df = pd.DataFrame(approved_data)
            st.dataframe(approved_df, use_container_width=True)
    
    # Rejected emergencies
    if not st.session_state.rejected_emergencies:
        st.info("No rejected emergencies yet.")
    else:
        st.markdown("### Rejected Emergencies")
        rejected_data = []
        for e, reason in st.session_state.rejected_emergencies:
            rejected_data.append({
                "ID": e.id,
                "Type": e.type.value.upper(),
                "Priority": e.priority,
                "Location": e.location,
                "Coordinates": f"{e.gps_lat}, {e.gps_lon}",
                "Reason for Rejection": reason
            })
        
        if rejected_data:
            rejected_df = pd.DataFrame(rejected_data)
            st.dataframe(rejected_df, use_container_width=True)

# Tab 4: Dispatch
with tab4:
    st.subheader("Emergency Dispatch")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Dispatch Settings")
        
        # Add unique keys to checkboxes
        send_email = st.checkbox("Send via Email", value=st.session_state.get("send_via_email", True), key="email_dispatch_checkbox")
        st.session_state.send_via_email = send_email
        
        send_slack = st.checkbox("Send to Slack", value=st.session_state.get("send_to_slack", False), key="slack_dispatch_checkbox")
        st.session_state.send_to_slack = send_slack
        
        # Email address input
        email = st.text_input("Dispatch Email:", value=st.session_state.get("email_address", "anuju760@gmail.com"), key="dispatch_email_input")
        st.session_state.email_address = email
        
        # Separate buttons for generating vs sending
        col1a, col1b = st.columns(2)
        with col1a:
            if st.button("Generate Email", disabled=not st.session_state.approved_emergencies, use_container_width=True, key="generate_email_button"):
                # Convert Emergency objects to dictionaries if needed
                try:
                    # Try the normal way first
                    approval_data = ApprovalData(
                        approved_emergencies=st.session_state.approved_emergencies,
                        rejection_notes="Rejected: None"
                    )
                except Exception as e:
                    # If that fails, try converting to dictionaries
                    approved_dicts = [emergency.dict() for emergency in st.session_state.approved_emergencies]
                    approval_data = ApprovalData(
                        approved_emergencies=approved_dicts,
                        rejection_notes="Rejected: None"
                    )
                st.session_state.email_content = create_detailed_dispatch_email(approval_data)
                st.success("✅ Email content generated")
        
        with col1b:
            if st.button("Send Dispatch", type="primary", disabled=not st.session_state.approved_emergencies, use_container_width=True, key="send_dispatch_button"):
                email_content, slack_result, notifications_sent = dispatch_emergencies()
                
                # Show summary notification based on what was sent
                if notifications_sent:
                    notification_types = " and ".join(notifications_sent)
                    st.success(f"✅ Dispatched via {notification_types}")
                    st.balloons()
                else:
                    st.warning("No notifications were sent. Please enable at least one notification method.")
    
    # Show dispatch preview
    if st.session_state.email_content:
        with st.expander("View Dispatch Email Content", expanded=True):
            st.text_area(
                "Email Content",
                st.session_state.email_content,
                height=400
            )
    

# Footer
st.markdown("---")
st.caption(f"DCRCA Agent Dashboard v1.0 | Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
st.caption(f"Built with ❤️ using Portia AI & WeMakeDevs - AgentHack2025")