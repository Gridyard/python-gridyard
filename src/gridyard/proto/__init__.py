"""Protobuf messages of ESP-IDF protocomm (session and Security 2).

The generated modules register their files in this private pool instead of the
default pool. The .proto files have no package, so names such as constants.proto or
Status would collide with other libraries in the same process.
"""

from google.protobuf import descriptor_pool

POOL = descriptor_pool.DescriptorPool()
