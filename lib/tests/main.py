import sys
import time

from amt.dtp.ise.lib.apiclient import *

client = ApiClient()


for i in range(50):
    start = time.time()
    client.application_ping()
    duration_s = time.time() - start
    print(f"Duration: {duration_s:.3f} s")
sys.exit(0)

client.asset_upload("ast2")
client.asset_spawn("ast2", "/test", [0.0, 0.0, 0.0])
client.asset_spawn("ast2", "/test2", [0.0, 0.0, 1.0])
client.asset_spawn("ast2", "/test3", [0.0, 1.0, 1.0])
sys.exit(0)

client.environment_close()
# print(f"config_path: {client.application_configuration_path()}")
# print(f"config:\n{client.application_configuration().model_dump_json(indent=2)}")
# print(f"working_directory: {client.application_working_directory()}")
client.environment_upload("env1")
client.asset_upload("ast1")
client.asset_upload("ast2")
print(f"Environments: {client.environment_list()}")
print(f"Assets: {client.asset_list()}")
client.environment_open("env1")
client.asset_spawn("ast1", "/test", [0.0, 0.0, 0.0])
time.sleep(2)

client.timeline_play()
time.sleep(2)
print(f"Timeline:\n{client.timeline_info().model_dump_json(indent=2)}")
time.sleep(2)
client.timeline_pause()
time.sleep(2)
client.timeline_stop()
time.sleep(2)

client.asset_remove("ast1")
client.environment_remove("env1")
print(f"Environments: {client.environment_list()}")
print(f"Assets: {client.asset_list()}")
time.sleep(2)
client.environment_close()
time.sleep(2)
print("done")
