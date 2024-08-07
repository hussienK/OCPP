import time

class ChargeSessionManager:
	def __init__(self, transaction_id, connector_id):
		self.transaction_id = id
		self.connector_id = connector_id
		self.start_time = time.time()
		self.last_activity_time = self.start_time
		self.last_heartbeat_time = self.start_time
		self.currently_charging = False

	def heartbeat(self):
		self.last_heartbeat_time = time.time()

	def activity_done(self, c_id):
		if self.connector_id == c_id:
			self.last_activity_time = time.time()

	def start_charging(self, c_id):
		if self.connector_id == c_id:
			self.currently_charging = True
			self.activity_done(c_id)

	def stop_charging(self, c_id):
		if self.connector_id == c_id:
			self.currently_charging = False
			self.activity_done(c_id)

	def check_timeouts(self, heartbeat_timeout, inactivity_timeout, idle_timeout, max_session_duration):
		current_time = time.time()
		if self.check_heartbeat_timeout(current_time, heartbeat_timeout):
			return "Heartbeat Timeout"
		if self.check_idle_timeout(current_time, idle_timeout):
			return "Idle Timeout"
		if self.check_inactivity_timeout(current_time, inactivity_timeout):
			return "Inactivity Timeout"
		if self.check_session_timeout(current_time, max_session_duration):
			return "Maximum Duration Timeout"
		return None

	def check_inactivity_timeout(self, current_time, inactivity_timeout):
		if (current_time - self.last_activity_time) >= inactivity_timeout:
			return True
		return False
	
	def check_idle_timeout(self, current_time, idle_timeout):
		if (current_time - self.last_activity_time) >= idle_timeout and not self.currently_charging:
			return True
		return False
	
	def check_session_timeout(self, current_time, max_session_duration):
		if (current_time - self.start_time > max_session_duration):
			return True
		return False
	
	def check_heartbeat_timeout(self, current_time, heartbeat_timeout):
		if (current_time - self.last_heartbeat_time) > heartbeat_timeout:
			return True
		return False

class ChargeMeterManager:
	def __init__(self, transaction_id, connector_id):
		self.price_per_kwh = 0.15
		self.meter_start = 0
		self.target_kwh = -1
		self.charged_kwh = 0

	