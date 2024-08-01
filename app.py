from datetime import datetime
import json
import os
import logging

from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus
from ocpp.v16 import call_result, call

from supabase import create_client, Client

import asyncio
from hypercorn.asyncio import serve
from hypercorn.config import Config
import websockets

from utils import *
from logger import create_logger


#load env variables
DB_URL = os.getenv("DB_URL")
DB_API = os.getenv("DB_API")
PORT = int(os.getenv("PORT"))
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
		self.price_per_kwh = 0.15
		self.meter_start = 0
		self.target_kwh = -1
		self.charged_kwh = 0





	"""
		BootNotifcation handler
		-sets the interval of heartbeats
		-sets the boot start time
	"""
	@on(Action.BootNotification)
	async def on_boot_notification(self, **kwargs):
		logging.info("BootNotification received: Vendor=%s, Model=%s\n\n", kwargs['charge_point_vendor'], kwargs['charge_point_model'])
		return call_result.BootNotification(
			current_time=datetime.now().isoformat(),
			interval=60,
			status=RegistrationStatus.accepted
		)
	




	"""
		Send a heartbeat to check of server still connected
	"""
	@on(Action.Heartbeat)
	async def on_heartbeat(self, **kwargs):
		logging.info("Heartbeat recieved")
		return call_result.Heartbeat(current_time = datetime.now().isoformat())
	




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
			if (datetime.now() < datetime.fromisoformat(user_data['expiry_date'])):
				self.authorized_users.add(id_token) #add the user to list of authenticated for quicker access
				id_token_info = {'status': AuthorizationStatus.accepted, 'expiryDate': datetime.now().isoformat()}
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
		return query_result.data[0]

	#remove the expired users from data and their related transaction
	def	remove_expired_user(self, id_tag):
		self.authorized_users.discard(id_tag)
		self.transactions_users.discard(id_tag)




	"""
		Does the start transaction
	"""
	@on(Action.StartTransaction)
	async def on_start_transaction(self, **kwargs):
		connector_id = kwargs['connector_id']
		point_data, user_data = self.get_charge_point_and_user_data(connector_id, kwargs['id_tag'])

		#if charger doesn't exit, or user not authenticated, or charge_point not available then request is blocked
		if not point_data or kwargs['id_tag'] not in self.authorized_users or point_data[0]['status'] != 'Available':
			return self.start_transaction_responce(0, AuthorizationStatus.blocked)
		
		#check for transaction if it's already active and return current transaction code
		if kwargs['id_tag'] in self.transactions_users:
			return self.concurrent_transaction_responce(kwargs['id_tag'])

		#starts the whole transaction process
		transaction_id = self.start_new_transaction(kwargs, user_data, point_data)

		#returns the expected result
		return self.start_transaction_responce(transaction_id, AuthorizationStatus.accepted)

	#get the information about the user and the charge_points
	def	get_charge_point_and_user_data(self, connector_id, id_tag):
		query_result_point = supabase.table('charge_points').select('id', 'status', 'meter_reading').eq('id', connector_id).execute()
		query_result_users = supabase.table('users').select('id').eq('id_tag', kwargs['id_tag']).execute()
		return query_result_point.data[0], query_result_users.data[0]

	#returns a response for transaction start with required data
	def	start_transaction_responce(self, transaction_id, status):
		return call_result.StartTransaction(
			transaction_id= transaction_id,
			id_tag_info={'status': AuthorizationStatus.blocked}
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
		self.update_charge_point_meter(kwargs['meter_start', point_data['id']])
		#creates a new session and transaction
		session_id = self.create_new_session(user_data['id'], point_data['id'])
		transaction_id = self.create_new_transaction(session_id)

		#checks if there is a charging_profile which indicates a charging limit
		self.meter_start = kwargs['meter_start']
		if hasattr(kwargs, "charging_profile"):
			amount_kwh = kwargs['charging_profile']['chargingSchedule']['chargingSchedulePeriod'][0]['limit'] / 1000
			self.target_kwh = amount_kwh
			self.charged_kwh = 0
			logging.info("Starting transaction with %s kWh", self.target_kwh)

		return transaction_id
	
	#updates the charge_points meter
	def update_charge_point_meter(self, meter_start, charge_point_id):
		supabase.table('charge_points').update({'meter_reading': meter_start,}).eq('id', charge_point_id).execute()

	#updates the table by creating a new session
	def create_new_session(self, user_id, charge_point_id):
		session_data = supabase.table('sessions').insert([{
				'user_id': user_id,
				'charge_point_id': charge_point_id,
				'start_time': datetime.now().isoformat(),
			}]).execute()
		return session_data[0]['id']
	
	#updates the table by creating a new transaction with random generated transactio key
	def create_new_transaction(self, session_id):
		transaction_id = generate_transaction_id() #generates a random transaction_id
		supabase.table('transactions').insert([{
				'id': transaction_id,
				'session_id': session_id,
			}]).execute()
		return transaction_id





	async def	close_transaction(self, transaction_id, id_tag, meter_stop):
		#gets new meter reading
		meter_now = meter_stop
		meter_amount = 0

		#queries the tables for all the required data
		query_result_transac = supabase.table('transactions').select('id', 'session_id').eq('id', transaction_id).execute()
		trancsac_data = query_result_transac.data
		query_result_session = supabase.table('sessions').select('id', 'charge_point_id').eq('id', trancsac_data[0]['session_id']).execute()
		session_data = query_result_session.data

		#update the sessions, and transactions db
		meter_amount = meter_now - self.meter_start
		supabase.table('sessions').update(
			{
				'energy_consumed': meter_amount,
				'end_time': datetime.now().isoformat(),
			}
		).eq('id', trancsac_data[0]['session_id']).execute()
		supabase.table('transactions').update(
			{
				'amount': meter_amount * self.price_per_kwh,
				'timestamp': datetime.now().isoformat(),
			}
		).eq('id', transaction_id).execute()

	"""
		Does the stopping of transactions, updates the database
	"""
	@on(Action.StopTransaction)
	async def	on_stop_transaction(self, **kwargs):

		#check if user does have an ongoing transactions
		if not (kwargs['id_tag'] in self.transactions_users):
			#returns accepted status
			return call_result.StopTransaction(
				{
					'status': AuthorizationStatus.invalid,
				}
			)

		self.transactions_users.remove(kwargs['id_tag'])
		await self.close_transaction(kwargs['transaction_id'], kwargs['id_tag'], kwargs['meter_stop'])
		session = supabase.table('sessions').select('charge_point_id', "id").eq('id',
				supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data[0]['session_id']).execute()
		supabase.table('charge_points').update(
			{
				'meter_reading': kwargs['meter_stop']
			}
		).eq('id',session.data[0]['charge_point_id']).execute()
		#returns accepted status
		return call_result.StopTransaction(
			{
				'status': AuthorizationStatus.accepted,
			}
		)
	
	async def	on_stop_transaction_meter(self, **kwargs):

		session = supabase.table('sessions').select('user_id', 'charge_point_id', "id").eq('id',
				supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data[0]['session_id']).execute().data
		id_tag = supabase.table('users').select('id', 'id_tag').eq('id', session[0]['user_id']).execute().data[0]['id_tag']
		#check if user does have an ongoing transactions
		if not (id_tag in self.transactions_users):
			#returns accepted status
			return call_result.StopTransaction(
				{
					'status': AuthorizationStatus.invalid,
				}
			)
		self.transactions_users.remove(id_tag)
		await self.close_transaction(kwargs['transaction_id'], id_tag, int(kwargs['meter_value'][0]['sampled_value'][0]['value']))
		#returns accepted status
		return call_result.StopTransaction(
			{
				'status': AuthorizationStatus.accepted,
			}
		)


	@on(Action.MeterValues)
	async def on_meter_values(self, **kwargs):
		meter_value = kwargs['meter_value'][0]['sampled_value'][0]['value']
		self.charged_kwh = (int(meter_value) - self.meter_start) / 1000

		transaction = supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data
		sessions = supabase.table('sessions').select('id', 'charge_point_id').eq('id', transaction[0]['session_id']).execute().data
		supabase.table('charge_points').update(
			{
				'meter_reading': meter_value
			}
		).eq('id', sessions[0]['charge_point_id']).execute()

		print_spaced(self.charged_kwh)
		print_spaced(self.target_kwh)
		print_spaced(kwargs)
		if self.target_kwh != -1 and self.charged_kwh >= self.target_kwh:
			await self.on_stop_transaction_meter(**kwargs)
		return call_result.MeterValues(
		)

	"""
		-TODO: update based on given status
	"""
	@on(Action.StatusNotification)
	async def on_status_notification(self, **kwargs):
		supabase.table('charge_points').update(
			{
				'status': kwargs['status'],
			}
		).eq('id', kwargs['connector_id']).execute()
		return call_result.StatusNotification(
		)

	async def on_disconnect(self, websocket):
		if (self.id in connected_charge_points):
			del connected_charge_points[self.id]
		logging.info("ChargePoint %s disconnected", self.id)
		if (len(self.transactions_users) != 0):
			for t in self.transactions_users:
				sessions = supabase.table('sessions').select('id', 'user_id', 'charge_point_id', 'end_time').is_('end_time', None).eq('user_id',
			 			supabase.table('users').select('id', 'id_tag').eq('id_tag', t).execute().data[0]['id']).execute().data
				charge_point = supabase.table('charge_points').select('id', 'meter_reading').eq('id', sessions[0]['charge_point_id']).execute().data
				transaction = supabase.table('transactions').select('id', 'session_id').eq('session_id', sessions[0]['id']).execute().data
				await self.close_transaction(transaction[0]['id'], t, charge_point[0]['meter_reading'])

async def stop_charging(self, transaction_id):
    await self.close_transaction(transaction_id, self.id_tag, self.charged_kwh * 1000)  # Convert kWh to Wh for meter stop value
    session = supabase.table('sessions').select('charge_point_id', "id").eq('id', 
            supabase.table('transactions').select('id', 'session_id').eq('id', transaction_id).execute().data[0]['session_id']).execute()
    supabase.table('charge_points').update(
        {
            'meter_reading': self.charged_kwh * 1000  # Convert kWh to Wh
        }
    ).eq('id', session.data[0]['charge_point_id']).execute()

    await send_remote_stop_transaction(self, transaction_id)

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

async def send_remote_stop_transaction(cp, transaction_id):
	request = call.RemoteStopTransaction(
		transaction_id=transaction_id
	)
	response = await cp.call(request)
	return response

async def start_remote_transaction(data):
	charge_point_id = data['data'].get('charge_point_id')
	id_tag = data['data'].get('id_tag')
	amount_kwh = data['data'].get('amount_kwh')

	if not charge_point_id or not id_tag:
		return json.dumps({'error': 'Missing charge_point_id or id_tag'})

	if charge_point_id in connected_charge_points:
		cp = connected_charge_points[charge_point_id]
		if (amount_kwh is not None):
			cp.target_kwh = amount_kwh
		connector_id = data.get('connector_id')
		response = None
		try:
			if connector_id is not None:
				connector_id = int(connector_id)
			else:
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

async def stop_remote_transaction(data):
	charge_point_id = data['data'].get('charge_point_id')
	transaction_id = data['data'].get('transaction_id')

	if not charge_point_id or not transaction_id:
		return json.dumps({'error': 'Missing charge_point_id or transaction_id'})

	if charge_point_id in connected_charge_points:
		cp = connected_charge_points[charge_point_id]
		response = None
		try:
			response = await (send_remote_stop_transaction(cp, transaction_id))
		except Exception as e:
			return json.dumps({'error': str(e)})

		response_dict = {
			'status': response.status if response else 'unknown',
			'message': 'Remote stop transaction initiated' if response else 'Failed to initiate transaction'
		}
		return json.dumps(response_dict)
	else:
		return json.dumps({'error': 'Charge point not connected'})

async def on_connect(websocket, path):
	""" For every new charge point that connects, create a ChargePoint instance
	and start listening for messages.
	"""
	charge_point_id = path.strip('/')
	if not charge_point_id.startswith("CP"):
		try:
			message = await websocket.recv()
			data = json.loads(message)
			if (data['action'] == 'start_charging'):
				response = await(start_remote_transaction(data))
				await websocket.send(json.dumps(response))
			elif (data['action'] == 'stop_charging'):
				response = await(stop_remote_transaction(data))
				await websocket.send(json.dumps(response))
		except Exception as e:
			print(f"Error in connection: {e}")
	else:
		cp = MyChargePoint(charge_point_id, websocket)
		connected_charge_points[charge_point_id] = cp
		logging.info("New connection established: %s", charge_point_id)
		try:
			await cp.start()
		except websockets.exceptions.ConnectionClosedError as e:
			logging.error("Connection closed with error: %s", e)
		finally:
			await cp.on_disconnect(websocket)

async def main():
	create_logger()

	# Start the WebSocket server
	server = await websockets.serve(
		on_connect,
		'0.0.0.0',
		PORT,
		subprotocols=['ocpp1.6']
	)

	try:
		await server.wait_closed(),
	except asyncio.CancelledError:
		print("Server is shutting down...")
		server.close()
		await server.wait_closed()

if __name__ == '__main__':
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print("Interrupted by user")