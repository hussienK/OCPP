from datetime import datetime, timezone
import json
import os
import logging
from queue import Queue

from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus, DataTransferStatus
from ocpp.v16 import call_result, call

from supabase import create_client, Client
from quart import Quart, request, jsonify, websocket

import asyncio
#import websockets

from utils import *
from logger import create_logger
from ChargeSessionManagers import ChargeSessionManager, ChargeMeterManager

#load env variables
# DB_URL = os.getenv("DB_URL")
# DB_API = os.getenv("DB_API")
# PORT = int(os.getenv("PORT"))
PORT = 9000
DB_URL = "https://gjiuhpvnfbpjjjglgzib.supabase.co"
DB_API = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdqaXVocHZuZmJwampqZ2xnemliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjIwMDg5NDEsImV4cCI6MjAzNzU4NDk0MX0.B2CDr48yxglPKG6uEfAt9OPj2K-ZmqVHSeW6Bb_SW70"
supabase: Client = create_client(DB_URL, DB_API)

if (DB_API is None or DB_URL is None or PORT is None):
	print("Error in retrieving in enviroment variables")
	exit(1)

#load the database
supabase: Client = create_client(DB_URL, DB_API)

#store all the connected charge points
connected_charge_points = {}

class MyChargePoint(cp):
	"""
		constructor for a ChargepointManager
		-creates a list of authorized users, and a price
	"""
	def __init__(self, id, connection):
		super().__init__(id, connection)
		self.authorized_users = set()
		self.transactions_users = set()
		self.sessions = dict()
		self.session_meters = dict()
		self.id = id
		self.ws = connection

	async def handle_message(self):
		try:
			while True:
					message = await self.ws.receive()
					if message == None:
						break
					await self.route_message(message)
		except Exception as e:
			logging.error(f"error in handle message {e}")
		finally:
			await self.on_disconnect(self.ws)
		

	"""
		BootNotifcation handler
		-sets the interval of heartbeats
		-sets the boot start time
	"""
	@on(Action.BootNotification)
	async def on_boot_notification(self, **kwargs):
		logging.info("BootNotification received: Vendor=%s, Model=%s\n\n", kwargs['charge_point_vendor'], kwargs['charge_point_model'])
		return call_result.BootNotification(
			current_time=datetime.now(timezone.utc).isoformat(),
			interval=60,
			status=RegistrationStatus.accepted
		)




	"""
		Data Transfer handler
		-recieves data transfer messages
	"""
	@on(Action.DataTransfer)
	async def on_data_transfer(self, **kwargs):
		logging.info("Data transfer recieved")
		return call_result.DataTransfer(call_result.DataTransferStatus.unknown_vendor_id)

	"""
		Send a heartbeat to check of server still connected
	"""
	@on(Action.Heartbeat)
	async def on_heartbeat(self, **kwargs):
		logging.info("Heartbeat recieved")
		for session in self.sessions.items():
			session[1].heartbeat()
		return call_result.Heartbeat(current_time = datetime.now(timezone.utc).isoformat())
	




	"""
		checks if the user is authorized
		-checks in list of authorized users and also compares dates to determine
	"""
	@on(Action.Authorize)
	async def on_authorize(self, **kwargs):
		#get data of user with current token from database
		id_token = kwargs.get('id_tag', 'NULL')
		user_data = self.get_user_data(id_token)

		if user_data and not(id_token in self.transactions_users):
			#if token haven't expired
			if (datetime.now(timezone.utc) < datetime.fromisoformat(user_data[0]['expiry_date'])):
				self.authorized_users.add(id_token) #add the user to list of authenticated for quicker access
				id_token_info = {'status': AuthorizationStatus.accepted, 'expiryDate': datetime.now(timezone.utc).isoformat()}
			#exists but expired
			else:
				self.remove_expired_user(id_token)
				id_token_info = {'status': AuthorizationStatus.expired}
		#doesn't exist
		else:
			id_token_info = {'status': AuthorizationStatus.invalid}
		#return the required info
		return call_result.Authorize(id_tag_info=id_token_info)

	#get data of current user from database
	def	get_user_data(self, id_tag):
		query_result = supabase.table('users').select('id', 'expiry_date').eq('id_tag', id_tag).execute()
		return query_result.data

	#remove the expired users from data and their related transaction
	def	remove_expired_user(self, id_tag):
		self.authorized_users.discard(id_tag)
		self.transactions_users.discard(id_tag)




	"""
		Does the start transaction
		-if there is a charging profile we start the transaction while also indic
	"""
	@on(Action.StartTransaction)
	async def on_start_transaction(self, **kwargs):
		connector_id = kwargs['connector_id']
		point_data, user_data = self.get_charge_point_and_user_data(connector_id, kwargs['id_tag'])

		#if charger doesn't exit, or user not authenticated, or charge_point not available then request is blocked
		if not point_data or kwargs['id_tag'] not in self.authorized_users or point_data['status'] != 'Available' or not self.check_main_chargepoint():
			return self.start_transaction_responce(0, AuthorizationStatus.blocked)

		#check for transaction if it's already active and return current transaction code
		if kwargs['id_tag'] in self.transactions_users or self.has_active_transaction(user_data, point_data):
			return self.concurrent_transaction_responce(kwargs['id_tag'])

		#starts the whole transaction process
		transaction_id = self.start_new_transaction(kwargs, user_data, point_data)

		#returns the expected result
		self.sessions[transaction_id] = ChargeSessionManager(transaction_id, connector_id)
		asyncio.create_task(self.check_timeouts())
		return self.start_transaction_responce(transaction_id, AuthorizationStatus.accepted)

	def check_main_chargepoint(self):
		query = supabase.table('charge_points').select('status').eq('id', self.id).eq('connector_id', 0).execute().data[0]
		if (query is None or query['status'] != 'Available'):
			return False
		return True

	#get the information about the user and the charge_points
	def	get_charge_point_and_user_data(self, connector_id, id_tag):
		query_result_point = supabase.table('charge_points').select('id', 'connector_id', 'status', 'meter_reading').eq('connector_id', connector_id).eq('id', self.id).execute()
		query_result_users = supabase.table('users').select('id').eq('id_tag', id_tag).execute()
		return query_result_point.data[0], query_result_users.data[0]

	#returns a response for transaction start with required data
	def	start_transaction_responce(self, transaction_id, status):
		return call_result.StartTransaction(
			transaction_id= transaction_id,
			id_tag_info={'status': status}
			)
	
	#returns a responce for concurrent users
	def concurrent_transaction_responce(self, id_tag):
		sessions = supabase.table('sessions').select('id', 'user_id', 'end_time').is_('end_time', None).eq('user_id',
						supabase.table('users').select('id', 'id_tag').eq('id_tag', id_tag).execute().data[0]['id']).execute().data
		transactions = supabase.table('transactions').select('id', 'session_id').eq('session_id', sessions[0]['id']).execute().data
		return call_result.StartTransaction(transaction_id= transactions[0]['id'], id_tag_info={'status': AuthorizationStatus.concurrent_tx})

	#main code for handling a transaction start
	def start_new_transaction(self, kwargs, user_data, point_data):
		#add user to operations and update meter in case of sync error
		self.transactions_users.add(kwargs['id_tag'])
		self.update_charge_point_meter(kwargs['meter_start'], point_data['connector_id'])
		#creates a new session and transaction
		session_id = self.create_new_session(user_data['id'], point_data['connector_id'])
		transaction_id = self.create_new_transaction(session_id)

		#checks if there is a charging_profile which indicates a charging limit
		self.session_meters[transaction_id] = ChargeMeterManager(transaction_id, kwargs['connector_id'])
		transaction_meter = self.session_meters[transaction_id]
		transaction_meter.meter_start = kwargs['meter_start']
		transaction_meter.target_kwh = -1
		if hasattr(kwargs, "charging_profile"):
			amount_kwh = kwargs['charging_profile']['chargingSchedule']['chargingSchedulePeriod'][0]['limit'] / 1000
			transaction_meter.target_kwh = amount_kwh
			transaction_meter.charged_kwh = 0
			logging.info("Starting transaction with %s kWh", transaction_meter.target_kwh)

		return transaction_id

	#updates the charge_points meter
	def update_charge_point_meter(self, meter_start, connector_id):
		supabase.table('charge_points').update({'meter_reading': meter_start,}).eq('id', self.id).eq('connector_id', connector_id).execute()

	#updates the table by creating a new session
	def create_new_session(self, user_id, connector_id):
		session_data = supabase.table('sessions').insert([{
				'user_id': user_id,
				'connector_id': connector_id,
				'charge_point_id': self.id,
				'start_time': datetime.now(timezone.utc).isoformat(),
			}]).execute()
		return session_data.data[0]['id']

	#updates the table by creating a new transaction with random generated transactio key
	def create_new_transaction(self, session_id):
		transaction_id = generate_transaction_id() #generates a random transaction_id
		supabase.table('transactions').insert([{
				'id': transaction_id,
				'session_id': session_id,
			}]).execute()
		return transaction_id

	#checks if a user has any active transactions
	def has_active_transaction(self, user_data, point_data):
		user_id = user_data['id']
		point_id = point_data['connector_id']
		session_data = supabase.table('sessions').select('end_time').eq('user_id', user_id).neq('charge_point_id', self.id).neq('connector_id', point_id).is_('end_time', None).execute().data
		if len(session_data) == 0:
			return False
		return True





	"""
		Does the stopping of transactions, updates the database with info
	"""
	@on(Action.StopTransaction)
	async def	on_stop_transaction(self, **kwargs):

		#check if user does have an ongoing transactions
		if (kwargs['id_tag'] not in self.transactions_users):
			#returns accepted status
			return self.stop_transaction_responce( AuthorizationStatus.invalid)

		#close transaction and update the meter
		self.transactions_users.remove(kwargs['id_tag'])
		await self.close_transaction(kwargs['transaction_id'], kwargs['id_tag'], kwargs['meter_stop'])
		session = supabase.table('sessions').select('connector_id', "id").eq('id',
			supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data[0]['session_id']).execute()
		self.update_charge_point_meter(kwargs['meter_stop'], session.data[0]['connector_id'])
		return self.stop_transaction_responce(AuthorizationStatus.accepted)
	
	def stop_transaction_responce(self, status):		
		return call_result.StopTransaction({'status': status})






	"""
		sends a status notification to update database with status of current chargepoint
	"""
	@on(Action.StatusNotification)
	async def on_status_notification(self, **kwargs):
		#update the table
		supabase.table('charge_points').update({'status': kwargs['status']}).eq('connector_id', kwargs['connector_id']).eq('id', self.id).execute()
		for session in self.sessions.items():
			if kwargs['status'] in ['Charging', 'SuspendedEV', 'SuspendedEVSE']:
				if kwargs['status'] == 'Charging':
					session[1].start_charging(kwargs['connector_id'])
				else:
					session[1].stop_charging(kwargs['connector_id'])
		return call_result.StatusNotification()





	"""
		sends meter data from client to udpate database
	"""
	@on(Action.MeterValues)
	async def on_meter_values(self, **kwargs):
		#get data and calculate
		transaction_meter = self.session_meters[kwargs['transaction_id']]
		meter_value = kwargs['meter_value'][0]['sampled_value'][0]['value']
		transaction_meter.charged_kwh = (int(meter_value) - transaction_meter.meter_start) / 1000

		transaction = supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data
		sessions = supabase.table('sessions').select('id', 'charge_point_id', 'connector_id').eq('id', transaction[0]['session_id']).execute().data
		supabase.table('charge_points').update({'meter_reading': meter_value}).eq('id', sessions[0]['charge_point_id']).eq('connector_id', sessions[0]['connector_id']).execute()

		#if there is a limit to charging
		if transaction_meter.target_kwh != -1 and transaction_meter.charged_kwh >= transaction_meter.target_kwh:
			await self.on_stop_transaction_meter(**kwargs)

		self.session[kwargs['transaction_id']].activity_done(kwargs['connector_id'])

		return call_result.MeterValues()



	"""
		Extra stuff
	"""

	#when a client disconnects
	async def on_disconnect(self, websocket):
		#remove current ChargePoint instance
		if (self.id in connected_charge_points):
			del connected_charge_points[self.id]
		logging.info("ChargePoint %s disconnected", self.id)

		#if there is an active transaction, loop through each transactions and close it
		if (len(self.transactions_users) != 0):
			for t in self.transactions_users:
				sessions = supabase.table('sessions').select('id', 'user_id', 'charge_point_id', 'connector_id', 'end_time').is_('end_time', None).eq('user_id',
						 supabase.table('users').select('id', 'id_tag').eq('id_tag', t).execute().data[0]['id']).execute().data
				charge_point = supabase.table('charge_points').select('id', 'connector_id', 'meter_reading').eq('id', sessions[0]['charge_point_id']).eq('connector_id', sessions[0]['connector_id']).execute().data
				transaction = supabase.table('transactions').select('id', 'session_id').eq('session_id', sessions[0]['id']).execute().data
				await self.close_transaction(transaction[0]['id'], t, charge_point[0]['meter_reading'])

	#closes a transaction
	async def	close_transaction(self, transaction_id, id_tag, meter_stop):
		#gets new meter reading
		meter_now = meter_stop
		meter_amount = 0

		#queries the tables for all the required data
		query_result_transac = supabase.table('transactions').select('id', 'session_id').eq('id', transaction_id).execute()
		trancsac_data = query_result_transac.data
		query_result_session = supabase.table('sessions').select('id', 'charge_point_id', 'connector_id').eq('id', trancsac_data[0]['session_id']).execute()
		session_data = query_result_session.data

		#update the sessions, and transactions db
		meter_amount = meter_now - self.session_meters[transaction_id].meter_start
		supabase.table('sessions').update({'energy_consumed': meter_amount, 'end_time': datetime.now(timezone.utc).isoformat()}).eq('id', trancsac_data[0]['session_id']).execute()
		supabase.table('transactions').update({'amount': meter_amount * self.session_meters[transaction_id].price_per_kwh, 'timestamp': datetime.now(timezone.utc).isoformat()}).eq('id', transaction_id).execute()

		del self.sessions[transaction_id]
		self.session_meters[transaction_id]

	#when stopping a transaction because charging is now full
	async def	on_stop_transaction_meter(self, **kwargs):
		#query for required data
		session = supabase.table('sessions').select('user_id', 'charge_point_id', "id").eq('id',
				supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data[0]['session_id']).execute().data
		id_tag = supabase.table('users').select('id', 'id_tag').eq('id', session[0]['user_id']).execute().data[0]['id_tag']
		#check if user does have an ongoing transactions
		if not (id_tag in self.transactions_users):
			#returns accepted status
			return call_result.StopTransaction({'status': AuthorizationStatus.invalid})
		self.transactions_users.remove(id_tag)
		await self.close_transaction(kwargs['transaction_id'], id_tag, int(kwargs['meter_value'][0]['sampled_value'][0]['value']))
		#returns accepted status
		return call_result.StopTransaction({'status': AuthorizationStatus.accepted})




	async def check_timeouts(self):
		while (True):
			for session in self.sessions.items():
				ans = session[1].check_timeouts(75, 85, 60, 120)
				if ans is not None:
					print_spaced(ans)

			await asyncio.sleep(1)


"""
	code to send remote requests to the client
"""
#sends a request for remote starting a transaction
async def send_remote_start_transaction(cp, id_tag, connector_id=1, amount_kwh=0):
	request = call.RemoteStartTransaction(
		id_tag = id_tag,
		connector_id=connector_id,
		charging_profile={
			'chargingProfilePurpose': 'TxProfile',
			'stackLevel': 1,
			'chargingProfileId': 10,
			'chargingProfileKind': 'Absolute',
			'chargingSchedule': {
				'chargingRateUnit': 'W',
				'chargingSchedulePeriod': [{'startPeriod': 0, 'limit': amount_kwh * 1000}]  # Convert kWh to Watts
			}
		}
	)
	response = await cp.call(request)
	return response

#denf a remote request for stoping a transaction
async def send_remote_stop_transaction(cp, transaction_id):
	request = call.RemoteStopTransaction(transaction_id=transaction_id)
	response = await cp.call(request)
	return response
 
"""
	manages the requests for remote  transactions
"""
#starts the remote transactions
async def start_remote_transaction(data) -> json:
	charge_point_id = data.get('charge_point_id')
	id_tag = data.get('id_tag')
	amount_kwh = data.get('amount_kwh')

	if not charge_point_id or not id_tag:
		return json.dumps({'error': 'Missing charge_point_id or id_tag'})


	if charge_point_id in connected_charge_points:
		cp = connected_charge_points[charge_point_id]
		if (amount_kwh is not None):
			cp.target_kwh = amount_kwh
		connector_id = data.get('connector_id')
		response = None
		try:
			connector_id = 1
			response = await send_remote_start_transaction(cp, id_tag, connector_id, amount_kwh)
		except Exception as e:
			return json.dumps({'error': str(e)})

		response_dict = {
			'status': response.status if response else 'unknown',
			'message': 'Remote start transaction initiated' if response else 'Failed to initiate transaction'
		}
		return json.dumps(response_dict)
	else:
		return json.dumps({'error': 'Charge point not connected'})

#stops the remote transactions
async def stop_remote_transaction(data) -> json:
	charge_point_id = data.get('charge_point_id')
	transaction_id = data.get('transaction_id')

	if not charge_point_id or not transaction_id:
		return json.dumps({'error': 'Missing charge_point_id or transaction_id'})

	if charge_point_id in connected_charge_points:
		cp = connected_charge_points[charge_point_id]
		response = None
		try:
			response = await send_remote_stop_transaction(cp, transaction_id)
		except Exception as e:
			return json.dumps({'error': str(e)})

		response_dict = {
			'status': response.status if response else 'unknown',
			'message': 'Remote stop transaction initiated' if response else 'Failed to initiate transaction'
		}
		return json.dumps(response_dict)
	else:
		return json.dumps({'error': 'Charge point not connected'})
	

app = Quart(__name__)

@app.websocket('/ws/<id>')
async def ws(id):
	charge_point_id = id
	print(f"MESSAGE RECIEVED WITH ID {charge_point_id}")
	if charge_point_id not in connected_charge_points:
		# Create a new MyChargePoint instance with the WebSocket object
		cp = MyChargePoint(charge_point_id, websocket._get_current_object())
		connected_charge_points[charge_point_id] = cp
		print(f"New connection established: {charge_point_id}")
		await cp.handle_message()
	else:
		cp = connected_charge_points[charge_point_id]
		print(f"Chargepoint: recieved action")
		await cp.handle_message()

# Set up logging
logging.basicConfig(level=logging.DEBUG)

@app.route('/start_charging', methods=['POST'])
async def start_charging():
	data = await request.get_json()
	charge_point_id = data.get('charge_point_id')
	id_tag = data.get('id_tag')
	amount_kwh = data.get('amount_kwh')

	if not charge_point_id or not id_tag:
		return jsonify({'error': 'Missing charge_point_id or id_tag'}), 400

	try:
		response = await start_remote_transaction({
		    "charge_point_id": charge_point_id,
		    "id_tag": id_tag,
		    "amount_kwh": amount_kwh
		})
		return jsonify({"response": json.loads(response)})
	except Exception as e:
		logging.error(f"Error in /start_charging: {e}")
		return jsonify({'error': str(e)}), 500

@app.route('/stop_charging', methods=['POST'])
async def stop_charging():
	data = await request.get_json()
	charge_point_id = data.get('charge_point_id')
	transaction_id = data.get('transaction_id')

	if not charge_point_id or not transaction_id:
		return jsonify({'error': 'Missing charge_point_id or transaction_id'}), 400

	try:
		response = await stop_remote_transaction({
			"charge_point_id": charge_point_id,
			"transaction_id": transaction_id
		})
		return jsonify({"response": json.loads(response)})
	except Exception as e:
		logging.error(f"Error in /stop_charging: {e}")
		return jsonify({'error': str(e)}), 500




"""
	the central system is supposed to send diagnostics file to the spicified location
"""
@app.route('/get_diagnostics', methods=["POST"])
async def get_diagnostics():
	data = await request.get_json()
	loc = data.get('location')
	ret = data.get('retries')
	ret_interval = data.get('retryInterval')
	start_time = data.get('startTime')
	stop_time = data.get('stopTime')

	req = call.GetDiagnostics(
		location=loc,
		retries=ret,
		retry_interval=ret_interval,
		start_time=start_time,
		stop_time=stop_time
	)
	response = await connected_charge_points['CP06'].call(req)
	file_name = response.file_name
	ans = jsonify({'fileName': file_name})
	return ans

@app.route('/firmware_status_notification', methods=["POST"])
async def get_firmware_status_notification():
	req = call.FirmwareStatusNotification()
	response = await connected_charge_points['CP06'].call(req)
	ans = jsonify({'status': response.status})
	return ans

async def main():
	create_logger()
	await app.run_task(host="localhost", port=PORT, debug=False)

if __name__ == '__main__':
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print("Interrupted by user")