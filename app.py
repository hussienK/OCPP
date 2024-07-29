from datetime import datetime
from logger import create_logger
import logging
from utils import *

from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp
from ocpp.v16.enums import Action, RegistrationStatus, AuthorizationStatus
from ocpp.v16 import call_result, call

from supabase import create_client, Client

import asyncio
import websockets
from flask import Flask, request, jsonify

#import the db
DB_URL = "https://gjiuhpvnfbpjjjglgzib.supabase.co"
DB_API = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdqaXVocHZuZmJwampqZ2xnemliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjIwMDg5NDEsImV4cCI6MjAzNzU4NDk0MX0.B2CDr48yxglPKG6uEfAt9OPj2K-ZmqVHSeW6Bb_SW70"
supabase: Client = create_client(DB_URL, DB_API)

app = Flask(__name__)
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

	"""
		BootNotifcation handler
		-sets the interval of heartbeats
		-TODO: save to db the booted device
		-TODO: error handling
	"""
	@on(Action.BootNotification)
	async def on_boot_notification(self, charge_point_vendor, charge_point_model, **kwargs):
		logging.info("BootNotification received: Vendor=%s, Model=%s\n\n", charge_point_vendor, charge_point_model)
		print_spaced(kwargs)
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
		return call_result.Heartbeat(
			   current_time = datetime.now().isoformat()
		)
	
	"""
		checks if the user is authorized
	"""
	@on(Action.Authorize)
	async def on_authorize(self, **kwargs):
		#get data of user with current token from database
		id_token = kwargs.get('id_tag', 'NULL')
		query_result = supabase.table('users').select('id', 'expiry_date').eq('id_tag', id_token).execute()
		user_data = query_result.data

		if user_data and not(id_token in self.transactions_users):
			#if token haven't expired
			if (datetime.now() < datetime.fromisoformat(user_data[0]['expiry_date'])):
				self.authorized_users.add(id_token) #add the user to list of authenticated for quicker access
				id_token_info = {
					'status': AuthorizationStatus.accepted,
					'expiryDate': datetime.now().isoformat()	
				}
			#exists but expired
			else:
				if id_token in self.authorized_users: #remove user from authentication if expired
					self.authorized_users.remove(id_token)
				if id_token in self.transactions_users: #remove user from transactions if expired
					self.transactions_users.remove(id_token)
				id_token_info = {
				'status': AuthorizationStatus.expired,
				}
		#doesn't exist
		else:
			id_token_info = {
			'status': AuthorizationStatus.invalid,
			}

		#return the required info
		return call_result.Authorize(
			id_tag_info=id_token_info
		)
	
	"""
		Does the start transaction
		-TODO: handle some edge cases, don't start if already started etc...
	"""
	@on(Action.StartTransaction)
	async def on_start_transaction(self, **kwargs):
		connector_id = kwargs['connector_id']

		#get the information about the user and the charge_points
		query_result = supabase.table('charge_points').select('id', 'status', 'meter_reading').eq('id', connector_id).execute()
		query_result_users = supabase.table('users').select('id').eq('id_tag', kwargs['id_tag']).execute()
		point_data = query_result.data
		user_data = query_result_users.data

		#if charger doesn't exit, or user not authenticated, or charge_point not available then request is blocked
		if (len(point_data) == 0 or kwargs['id_tag'] not in self.authorized_users
	  			or point_data[0]['status'] != 'Available'):
			return call_result.StartTransaction(
				transaction_id= 0,
				id_tag_info={
					'status': AuthorizationStatus.blocked,
				}
			)
		#check for transaction if it's already active and return current transaction code
		if kwargs['id_tag'] in self.transactions_users:
			sessions = supabase.table('sessions').select('id', 'user_id', 'end_time').is_('end_time', None).eq('user_id',
			 								supabase.table('users').select('id', 'id_tag').eq('id_tag', kwargs['id_tag']).execute().data[0]['id']).execute().data
			transactions = supabase.table('transactions').select('id', 'session_id').eq('session_id', sessions[0]['id']).execute().data
			return call_result.StartTransaction(
				transaction_id= transactions[0]['id'],
				id_tag_info={
					'status': AuthorizationStatus.blocked,
				}
			)
		
		self.transactions_users.add(kwargs['id_tag'])
		#updates the table by creating a new session
		create_table_d = supabase.table('sessions').insert(
			[{
				'user_id': user_data[0]['id'],
				'charge_point_id': point_data[0]['id'],
				'start_time': datetime.now().isoformat(),
			}]
		).execute()
		#updates the charge_points meter
		supabase.table('charge_points').update(
			{
				'meter_reading': kwargs['meter_start'],
			}
		).eq('id', connector_id).execute()
		#updates the transaction table by creating a new transaction
		session_id = create_table_d.data[0]['id']
		transaction_id = generate_transaction_id() #generates a random transaction_id
		supabase.table('transactions').insert(
			[{
				'id': transaction_id,
				'session_id': session_id,
			}]
		).execute()

		self.meter_start = kwargs['meter_start']
		#returns the expected result
		return call_result.StartTransaction(
				transaction_id= transaction_id,
				id_tag_info={
					'status': AuthorizationStatus.accepted,
				}
		)

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
		
		#returns accepted status
		return call_result.StopTransaction(
			{
				'status': AuthorizationStatus.accepted,
			}
		)

	@on(Action.MeterValues)
	async def on_meter_values(self, **kwargs):
		transaction = supabase.table('transactions').select('id', 'session_id').eq('id', kwargs['transaction_id']).execute().data
		sessions = supabase.table('sessions').select('id', 'charge_point_id').eq('id', transaction[0]['session_id']).execute().data
		supabase.table('charge_points').update(
			{
				'meter_reading': kwargs['meter_value'][0]['sampled_value'][0]['value']
			}
		).eq('id', sessions[0]['charge_point_id']).execute()
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
		logging.info("ChargePoint %s disconnected", self.id)
		if (len(self.transactions_users) != 0):
			for t in self.transactions_users:
				sessions = supabase.table('sessions').select('id', 'user_id', 'charge_point_id', 'end_time').is_('end_time', None).eq('user_id',
			 			supabase.table('users').select('id', 'id_tag').eq('id_tag', t).execute().data[0]['id']).execute().data
				charge_point = supabase.table('charge_points').select('id', 'meter_reading').eq('id', sessions[0]['charge_point_id']).execute().data
				transaction = supabase.table('transactions').select('id', 'session_id').eq('session_id', sessions[0]['id']).execute().data
				await self.close_transaction(transaction[0]['id'], t, charge_point[0]['meter_reading'])


async def send_remote_start_transaction(cp, id_tag, connector_id=1):
	request = call.RemoteStartTransaction(
		id_tag = id_tag,
		connector_id=connector_id
	)
	responce = await cp.call(request)
	return responce

@app.route('/start_charging', methods=['POST'])
def	start_charging():
	data = request.json
	charge_point_id = data.get('charge_point_id')
	id_tag = data.get('id_tag')

	if not charge_point_id or not id_tag:
		return jsonify({'error': 'Missing charge_point_id or id_tag'}), 400
	
	if charge_point_id in connected_charge_points:
		cp = connected_charge_points[charge_point_id]
		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)
		responce = loop.run_until_complete(send_remote_start_transaction(cp, id_tag, charge_point_id))
		return jsonify(responce.to_dict())
	else:
		return jsonify({'error': 'Charge point not connected'}), 400

async def on_connect(websocket, path):
	""" For every new charge point that connects, create a ChargePoint instance
	and start listening for messages.
	"""
	
	charge_point_id = path.strip('/')
	cp = MyChargePoint(charge_point_id, websocket)
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
        'localhost',
        9000,
        subprotocols=['ocpp1.6']
    )

    # Handle server shutdown
    try:
        await server.wait_closed()
    except asyncio.CancelledError:
        print("Server is shutting down...")
        server.close()
        await server.wait_closed()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")