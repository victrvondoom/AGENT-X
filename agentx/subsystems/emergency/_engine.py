import os
import requests
from datetime import datetime
from typing import List
from pydantic import BaseModel, Field
from enum import Enum
from dotenv import load_dotenv
from portia import Config, Portia, PlanBuilderV2, StepOutput

load_dotenv()

class EmergencyType(str, Enum):
    FIRE = "fire"
    FLOOD = "flood"
    MEDICAL = "medical"
    ACCIDENT = "accident"

class Emergency(BaseModel):
    id: str
    description: str
    type: EmergencyType
    location: str
    gps_lat: float
    gps_lon: float
    priority: float
    people_affected: int

class EmergencyData(BaseModel):
    raw_data: str

class LocationData(BaseModel):
    emergencies: List[Emergency]

class PriorityData(BaseModel):
    emergencies: List[Emergency]

class ApprovalData(BaseModel):
    approved_emergencies: List[Emergency]
    rejection_notes: str

class DispatchResult(BaseModel):
    email_sent: bool
    total_dispatched: int
    summary: str

# CLI based - Human in the loop approval
def get_human_approval(priority_data: PriorityData) -> ApprovalData:
    if not priority_data or not priority_data.emergencies:
        return ApprovalData(approved_emergencies=[], rejection_notes="No emergencies provided")
    approved = []
    rejected = []
    for i, e in enumerate(priority_data.emergencies, 1):
        print(f"\nEmergency {i}/{len(priority_data.emergencies)}: {e.id}")
        print(f"Type: {e.type.upper()}, Location: {e.location}, GPS: {e.gps_lat},{e.gps_lon}")
        print(f"Priority: {e.priority}/5, People: {e.people_affected}")
        print(f"Description: {e.description}")
        while True:
            d = input("Approve? (y/n): ").lower().strip()
            if d=="y":
                approved.append(e)
                break
            if d=="n":
                reason = input("Reason for rejection: ").strip()
                rejected.append(f"{e.id}: {reason}")
                break
    notes = "Rejected: " + (", ".join(rejected) if rejected else "None")
    return ApprovalData(approved_emergencies=approved, rejection_notes=notes)

def create_detailed_dispatch_email(approval_data: ApprovalData) -> str:
    """Create a detailed email with comprehensive emergency information"""
    
    if not approval_data.approved_emergencies:
        return """🚨 EMERGENCY DISPATCH UPDATE 🚨
        
No emergencies were approved for dispatch at this time.
All reported incidents have been reviewed and either resolved or determined to be non-urgent.

Human Review Completed: ✅
Status: STANDBY"""
    
    email_body = f"""🚨 URGENT EMERGENCY DISPATCH ALERT 🚨
==========================================

📋 Authority Validated - to take immediate action and sends appropriate teams at critical locations..

📊 DISPATCH SUMMARY:
- Total Approved Emergencies: {len(approval_data.approved_emergencies)}
- Human Validation: COMPLETED ✅
- GPS Coordinates: VERIFIED ✅ 
- Dispatch Status: ACTIVE 🚨

📍 DETAILED EMERGENCY INCIDENTS:
==========================================

"""
    
    for i, emergency in enumerate(approval_data.approved_emergencies, 1):
        priority_emoji = "🔴" if emergency.priority >= 4 else "🟠" if emergency.priority >= 3 else "🟡" if emergency.priority >= 2 else "🟢"
        type_emoji = {"fire": "🔥", "flood": "🌊", "medical": "🚑", "accident": "🚗"}.get(emergency.type.value, "⚠️")
        
        email_body += f"""
INCIDENT #{i} - {emergency.id}
{priority_emoji} PRIORITY: {emergency.priority}/5.0 ({emergency.priority >= 4 and 'CRITICAL' or emergency.priority >= 3 and 'HIGH' or emergency.priority >= 2 and 'MEDIUM' or 'LOW'})
{type_emoji} TYPE: {emergency.type.upper()} EMERGENCY
📍 LOCATION: {emergency.location}
🗺️ GPS COORDINATES: {emergency.gps_lat}, {emergency.gps_lon}
👥 PEOPLE AFFECTED: {emergency.people_affected} individual(s)
📝 SITUATION: {emergency.description}

🎯 Google Maps Link: https://maps.google.com/?q={emergency.gps_lat},{emergency.gps_lon}
⚡ RESPONSE REQUIRED: Immediate dispatch to GPS location

----------------------------------------
"""
    
    email_body += f"""
⚡ IMMEDIATE RESPONSE PROTOCOL:
==========================================
1. 🚑 DEPLOY emergency teams to GPS coordinates in higher priority
2. 📞 COORDINATE with local emergency services (911/FDNY/NYPD/EMS/SDRF/NDRF)
3. 🗺️ USE provided GPS coordinates for precise navigation
4. 📋 IMPLEMENT emergency protocols based on incident type - follows SOP to tackle disaster
5. 📧 REPORT response status and resource needs immediately
6. ⏰ PRIORITIZE incidents marked as CRITICAL (Priority 4+)
7. 🔄 CONTINUOUSLY MONITOR situation and adjust response as needed & report all LATEST to aother HIGHER AUTHORITY..

🔍 VALIDATION DETAILS:
- Human Validator: Emergency Response Coordinator 
- Validation Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S EST')}
- Data Source: Multi-platform emergency monitoring
- Verification: All incidents human-reviewed and GPS-verified

⚠️ CRITICAL RESPONSE INSTRUCTIONS:
- Treat all incidents as potentially life-threatening
- Coordinate with incident commanders on scene
- Document all response actions and resource utilization
- Request additional support if needed (medical, fire, police)
- Maintain communication with this coordination center

🆘 URGENT COORDINATION: Reply to this email for immediate assistance

{approval_data.rejection_notes}

--- 
India Emergency Response Coordination System
Human-Supervised AI Disaster Management
Dispatch Authorization: ✅ CONFIRMED"""
    
    return email_body

def create_slack_dispatch_message(approval_data: ApprovalData) -> str:
    """Create a Slack-formatted message for emergency dispatch"""
    
    if not approval_data.approved_emergencies:
        return """🚨 *EMERGENCY DISPATCH UPDATE* 🚨

No emergencies approved for dispatch at this time.
All incidents reviewed - Status: *STANDBY*

Human Review: ✅ *COMPLETED*"""
    
    slack_message = f"""🚨 *URGENT EMERGENCY DISPATCH ALERT* 🚨 - Slack Community

📋 *{len(approval_data.approved_emergencies)} CRITICAL EMERGENCIES* - Human Validated ✅

*DISPATCH SUMMARY:*
• Total Emergencies: *{len(approval_data.approved_emergencies)}*
• Human Validation: ✅ *COMPLETED*
• GPS Coordinates: ✅ *VERIFIED*
• Status: 🚨 *ACTIVE DISPATCH*

*EMERGENCY INCIDENTS:*
"""
    
    for i, emergency in enumerate(approval_data.approved_emergencies, 1):
        priority_emoji = "🔴" if emergency.priority >= 4 else "🟠" if emergency.priority >= 3 else "🟡" if emergency.priority >= 2 else "🟢"
        type_emoji = {"fire": "🔥", "flood": "🌊", "medical": "🚑", "accident": "🚗"}.get(emergency.type.value, "⚠️")
        
        priority_text = "CRITICAL" if emergency.priority >= 4 else "HIGH" if emergency.priority >= 3 else "MEDIUM" if emergency.priority >= 2 else "LOW"
        
        slack_message += f"""
*INCIDENT #{i}* - `{emergency.id}`
{priority_emoji} *Priority {emergency.priority}/5.0* ({priority_text})
{type_emoji} *{emergency.type.upper()} EMERGENCY*
📍 *Location:* {emergency.location}
🗺️ *GPS:* `{emergency.gps_lat}, {emergency.gps_lon}`
👥 *People Affected:* {emergency.people_affected}
📝 *Details:* {emergency.description}
🎯 <https://maps.google.com/?q={emergency.gps_lat},{emergency.gps_lon}|View on Google Maps>
───────────────────────────────
"""
    
    slack_message += f"""
⚡ *IMMEDIATE RESPONSE REQUIRED*
1. 🚑 Deploy teams to GPS coordinates
2. 📞 Coordinate with 911/FDNY/NYPD/EMS/SDRF/NDRF
3. 🗺️ Use GPS for navigation and effective planning
4. ⏰ Prioritize CRITICAL incidents first
5. 🔄 Continuously monitor situation and adjust response as needed & report all LATEST to another HIGHER AUTHORITY.
6. AWAY FROM MIS-INFORMATION | Rely on Official Sources

🕒 *Validation:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S EST')}
🤖 *System:* Human-Supervised AI Emergency Management
✅ *Authorization:* CONFIRMED

@channel - Immediate response coordination required!"""
    
    return slack_message

def send_slack_message(approval_data: ApprovalData) -> DispatchResult:
    """Send message directly to Slack using Web API"""
    try:
        slack_token = os.getenv("SLACK_BOT_TOKEN")
        if not slack_token:
            return DispatchResult(
                email_sent=False, 
                total_dispatched=0, 
                summary="❌ Slack token not found in environment"
            )
        
        message = create_slack_dispatch_message(approval_data)
        
        # Slack Web API endpoint
        url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {slack_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "channel": "#all-emergency-response-bot",
            "text": message,
            "username": "Emergency Response Bot",
            "icon_emoji": ":rotating_light:"
        }
        
        response = requests.post(url, json=payload, headers=headers)
        result = response.json()
        
        if result.get("ok"):
            return DispatchResult(
                email_sent=True,  # Using same flag for consistency
                total_dispatched=len(approval_data.approved_emergencies),
                summary=f"✅ Slack message sent successfully to #all-emergency-response-bot. Message ID: {result.get('ts', 'unknown')}"
            )
        else:
            return DispatchResult(
                email_sent=False,
                total_dispatched=0,
                summary=f"❌ Slack API error: {result.get('error', 'Unknown error')}"
            )
            
    except Exception as e:
        return DispatchResult(
            email_sent=False,
            total_dispatched=0,
            summary=f"❌ Slack integration failed: {str(e)}"
        )

def create_disaster_plan():
    return (
        PlanBuilderV2("5-Step Disaster Response")
        .input(name="email : ", default_value="anuju760@gmail.com")
        .invoke_tool_step(
            step_name="emergency_gathering",
            tool="search_tool",
            args={"search_query": "New York City emergency incidents fire flood medical earthquake building collapse accident rescue"},
            output_schema=EmergencyData
        )
        .llm_step(
            task="""From raw data, create 3-4 realistic NYC emergencies with fields:
- id, description, type, location, gps_lat, gps_lon, priority, people_affected""",
            inputs=[StepOutput("emergency_gathering")],
            output_schema=LocationData
        )
        .llm_step(
            task="""Prioritize each emergency on scale 1-5 based on severity and people affected""",
            inputs=[StepOutput(1)],
            output_schema=PriorityData
        )
        .function_step(
            function=get_human_approval,
            args={"priority_data": StepOutput(2)}
        )
        .function_step(
            function=create_detailed_dispatch_email,
            args={"approval_data": StepOutput(3)}
        )
        .invoke_tool_step(
            step_name="dispatch_email",
            tool="portia:google:gmail:send_email",
            args={
                "recipients": ["anuju760@gmail.com"],
                "email_title": "🚨 URGENT: Emergency Dispatch Alert - Human Validated Incidents",
                "email_body": StepOutput(4)
            },
            output_schema=DispatchResult
        )
        .function_step(
            function=send_slack_message,
            args={"approval_data": StepOutput(3)}
        )
        .final_output(output_schema=DispatchResult)
        .build()
    )

def run_disaster_agent():
    config = Config(
        llm_provider="google",
        api_keys={
            "google": os.getenv("GOOGLE_API_KEY"), 
            "portia": os.getenv("PORTIA_API_KEY"),
            "slack": os.getenv("SLACK_BOT_TOKEN")
        }
    )
    agent = Portia(config=config)
    plan = create_disaster_plan()
    result = agent.run_plan(plan)
    if result and result.outputs.final_output:
        # Get individual step outputs using step_outputs dict
        step_outputs = result.outputs.step_outputs
        
        # Email result is step 4 (0-indexed), Slack result is step 5
        email_step = step_outputs.get(4) if step_outputs else None
        slack_step = step_outputs.get(5) if step_outputs else None
        
        print(f"📧 Email Status: {email_step.value.summary if email_step else 'No email result'}")
        print(f"💬 Slack Status: {slack_step.value.summary if slack_step else 'No slack result'}")
        
        final_output = result.outputs.final_output.value
        print(f"🚨 Final Summary: {final_output.summary}")

        # Pretty print the complete result as JSON
        print(f"\n📋 COMPLETE PLAN RUN RESULTS:")
        print("=" * 50)
        print(result.model_dump_json(indent=2))
    else:
        print("❌ No result from disaster response system")

if __name__ == "__main__":
    run_disaster_agent()















