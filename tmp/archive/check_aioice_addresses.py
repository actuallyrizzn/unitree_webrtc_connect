#!/usr/bin/env python3
"""Check what addresses aioice sees"""
import asyncio
import aioice

async def main():
    conn = aioice.Connection(ice_controlling=False)
    addrs = await conn._get_host_addresses()
    print("aioice sees these host addresses:")
    for addr in addrs:
        print(f"  {addr}")

if __name__ == "__main__":
    asyncio.run(main())

