import logging

class CustomFormatter(logging.Formatter):
    def format(self, record):
        log_message = super().format(record)
        return f"{log_message}\n\n"

def create_logger():
	# Create a custom handler and formatter
	handler = logging.StreamHandler()
	formatter = CustomFormatter('%(levelname)s: %(message)s')
	handler.setFormatter(formatter)

	# Set up the root logger
	logger = logging.getLogger()
	logger.setLevel(logging.INFO)
	logger.handlers.clear()  # Remove any existing handlers
	logger.addHandler(handler)
	logging.getLogger('hypercorn.access').disabled = True