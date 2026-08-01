import azure.functions as func
from functions.blob_created import blob_bp

# Single entry point for the Python v2 programming model.
# Individual functions live inside blueprints so new triggers can be added
# without modifying this bootstrap file.
app = func.FunctionApp()

# Registers every function decorated inside blob_bp with the Functions host
# during worker startup.
app.register_functions(blob_bp)
