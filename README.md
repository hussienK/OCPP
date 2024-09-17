# Charge Point Management System (CPMS)

## Overview
- The Charge Point Management System (CPMS) is a Python-based system designed to manage electric vehicle (EV) charging sessions. It communicates with charge points using the Open Charge Point Protocol (OCPP) 1.6j over WebSocket. The CPMS performs key operations like session management, controlling operations, and tracking data from the chargers.
- It was built as part of my internship with an Electric Charging Solution Company.

## Project Description
This system implements the following OCPP 1.6j operations:
- **Authorize**
- **BootNotification**
- **Heartbeat**
- **MeterValues**
- **RemoteStartTransaction**
- **RemoteStopTransaction**
- **StartTransaction**
- **StatusNotification**
- **StopTransaction**

### System Requirements
1. **Session Management:** Handle and track charging sessions.
2. **Data Utilization:** Use [Supabase](https://supabase.com) for data storage and management.
3. **API Development:** Two main APIs:
   - **Start Charging:** Initiate charging with a specified amount of kWh.
   - **Stop Charging:** Terminate an ongoing charging session.
4. **Internet Accessibility:** The system must be hosted online for remote testing.

### Testing
To validate system performance, testing will be conducted using a charger simulator that mimics the behavior of real-world charge points.

## Deliverables
- A fully functional CPMS developed in Python.
- Implementation of the specified OCPP 1.6j operations.
- Integration with Supabase for session and data management.
- Two APIs for controlling charge sessions (start and stop).
- Hosting the server online for remote access.
- Successful testing using a charger simulator.

## Learning Outcomes
This project offers hands-on experience in:
- Implementing OCPP protocols.
- Developing a server to manage EV charging points.
- Integrating data storage solutions.
- Developing APIs and deploying server systems online.

---
**Note:** For more information on setting up the development environment and running the system, please contact me hussienkenaan93@gmail.com.
