from datetime import datetime, timedelta, timezone
import random


def	print_spaced(a):
	print("--------------------------------------------")
	print('\n\n\n\n\n')
	print(a)
	print('\n\n\n\n\n')
	print("--------------------------------------------")

def generate_transaction_id():
	return random.randrange(1, 10000)