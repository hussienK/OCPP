from datetime import datetime, timedelta, timezone
from supabase import Client, create_client
import time
import random

itemKeyCounter = 0

def	print_spaced(a):
	print("--------------------------------------------")
	print('\n\n\n\n\n')
	print(a)
	print('\n\n\n\n\n')
	print("--------------------------------------------")

# User creation function
def create_user(id_tag, name, expiry_duration_days):
	DB_URL = "https://gjiuhpvnfbpjjjglgzib.supabase.co"
	DB_API = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdqaXVocHZuZmJwampqZ2xnemliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjIwMDg5NDEsImV4cCI6MjAzNzU4NDk0MX0.B2CDr48yxglPKG6uEfAt9OPj2K-ZmqVHSeW6Bb_SW70"
	supabase: Client = create_client(DB_URL, DB_API)
	expiry_date = (datetime.now() + timedelta(days=expiry_duration_days)).isoformat()
	user_data = {
		'id_tag': id_tag,
		'name': name,
		'expiry_date': expiry_date
	}
	response = supabase.table('users').insert(user_data).execute()
	return response

def create_charge_point(location, status, manuf, firmware_version, meter_reading):
	DB_URL = "https://gjiuhpvnfbpjjjglgzib.supabase.co"
	DB_API = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdqaXVocHZuZmJwampqZ2xnemliIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjIwMDg5NDEsImV4cCI6MjAzNzU4NDk0MX0.B2CDr48yxglPKG6uEfAt9OPj2K-ZmqVHSeW6Bb_SW70"
	supabase: Client = create_client(DB_URL, DB_API)
	charge_point_data = {
		'location': location,
		'status': status,
		'manufacturer': manuf,
		'firmware_version': firmware_version,
		'meter_reading': meter_reading,
	}
	response = supabase.table('charge_points').insert(charge_point_data).execute()
	return response

def generate_transaction_id():
	return random.randrange(1, 10000)