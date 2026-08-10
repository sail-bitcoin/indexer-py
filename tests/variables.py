block_a = {
    # [block]
    "hash": "000000000000000000002bb58bd9225e26120abfab13434310c3252cfa5a982e",
    "confirmations": 813,
    "height": 957354,
    # to drop
    "target": "000000000000000000021a420000000000000000000000000000000000000000",
    "nextblockhash": "000000000000000000006ba2d785d45d12dd37b64d07daf42c412ff980d78cba",
    # [coinbasetx]
    "coinbase_tx": {
        "locktime": 1106486620,
        "coinbase": "03aa9b0e2cfabe6d6d0282b99a5255e3a7f4ce5a902045550078aafc056ad84b1e20942c3a1b2f1a9710000000f09f909f092f4632506f6f6c2f640000000000000000000000000000000000000000000000000000000000000000000000050000302800",
        # to drop
        "witness": "0000000000000000000000000000000000000000000000000000000000000000",
    },
    # [transaction]
    "tx": [
        {
            "txid": "d0ffab171fc254ed0b2ad71e17c9456633a1ded99bec7ec19fe9f5250a43902f",
            "hash": "c322b18e456e6fbea12f0b062c71e91efb456467ff64b46a22854d2a9ff2e520",
            # [input]
            "vin": [
                {
                    "coinbase": "03aa9b0e2cfabe6d6d0282b99a5255e3a7f4ce5a902045550078aafc056ad84b1e20942c3a1b2f1a9710000000f09f909f092f4632506f6f6c2f640000000000000000000000000000000000000000000000000000000000000000000000050000302800",
                }
            ],
            # [output]
            "vout": [
                {
                    "value": 0.00000546,
                    "n": 0,
                    "scriptPubKey": {
                        "hex": "76a914c6740a12d0a7d556f89782bf5faf0e12cf25a63988ac",
                        "type": "pubkeyhash",
                    },
                },
            ],
        }
    ],
}

block_b = {
    # [block]
    "hash": "0000000000000000000052df80fd8f952098615bd54373232db92f68a029eeb8",
    "confirmations": 1241,
    "height": 957350,
    # to drop
    "target": "000000000000000000021a420000000000000000000000000000000000000000",
    "nextblockhash": "00000000000000000001dfe2a1a8dbe402bf58a02ac537270b6faec41f368bf3",
    # [coinbasetx]
    "coinbase_tx": {
        "sequence": 4294967295,
        "coinbase": "03a69b0e1c3c204f4345414e2e58595a203e0f53696d706c65204d696e696e6700071392104a2e9652",
        # to drop
        "witness": "0000000000000000000000000000000000000000000000000000000000000000",
    },
    # [transaction]
    "tx": [
        {
            "txid": "4141f216c50a0859f6cac757bf4f1ad4b3831467667525966d8cc67b0dfabaa9",
            "hash": "6fa1ca6c40e2fa2d014be2d27448a1681cc4f0be196508a15f8453971438e322",
            # [inputs]
            "vin": [
                {
                    "coinbase": "03a69b0e1c3c204f4345414e2e58595a203e0f53696d706c65204d696e696e6700071392104a2e9652",
                    "txinwitness": ["0000000000000000000000000000000000000000000000000000000000000000"],
                    "sequence": 4294967295,
                }
            ],
            # [outputs]
            "vout": [
                {
                    "value": 0.0,
                    "n": 0,
                    "scriptPubKey": {
                        "asm": "OP_RETURN 4f434231c16063ee52000000740c0000d99e3011",
                        "desc": "raw(6a144f434231c16063ee52000000740c0000d99e3011)#u7na36qq",
                        "hex": "6a144f434231c16063ee52000000740c0000d99e3011",
                        "type": "nulldata",
                    },
                },
                {
                    "value": 0.59932796,
                    "n": 1,
                    "scriptPubKey": {
                        "asm": "0 1785306913ebe204303194aac203a572a7cf1c16",
                        "desc": "addr(bc1qz7znq6gna03qgvp3jj4vyqa9w2nu78qkwzds2n)#hdjv7e29",
                        "hex": "00141785306913ebe204303194aac203a572a7cf1c16",
                        "address": "bc1qz7znq6gna03qgvp3jj4vyqa9w2nu78qkwzds2n",
                        "type": "witness_v0_keyhash",
                    },
                },
            ],
        },
        {
            "txid": "294484c9cffccd97b229f3e4c2f23d9a8d660a2d5425661b804c8b99190ae5f0",
            "hash": "6f828065ac9659ddc986a0b30f20e99290b61318a7df5f1b0393abd237a97a81",
            "version": 1,
            "size": 223,
            "vsize": 141,
            "weight": 562,
            "locktime": 0,
            "vin": [
                {
                    "txid": "897641667a24091ad60bc23464cc5da2a3d52de20a51033680e8cf0a35040a90",
                    "vout": 0,
                    "scriptSig": {"asm": "", "hex": ""},
                    "txinwitness": ["3045022100b069e6616dea0e0eac2cd0c9cb11e70ea0248903d07e786a2ee7b1fdfca1569d02205db21fcd1cf2357ca08f37ffed7a104f76aee89526510e1f567d713c3bcb732e01", "02b4ea32721b8f3b3a84816c246802508c02460189aaa28536b5e82cb120cf2beb"],
                    "sequence": 4294967295,
                }
            ],
            "vout": [
                {
                    "value": 0.0035,
                    "n": 0,
                    "scriptPubKey": {
                        "asm": "0 8548de2901afd06de6a6227b43cb2353994a0116",
                        "desc": "addr(bc1qs4ydu2gp4lgxme4xyfa58jer2wv55qgkw02e4c)#7qdvs0m0",
                        "hex": "00148548de2901afd06de6a6227b43cb2353994a0116",
                        "address": "bc1qs4ydu2gp4lgxme4xyfa58jer2wv55qgkw02e4c",
                        "type": "witness_v0_keyhash",
                    },
                },
                {
                    "value": 0.00400673,
                    "n": 1,
                    "scriptPubKey": {
                        "asm": "0 b6c984605d175e1b5da6b4a1f57e68c01f891440",
                        "desc": "addr(bc1qkmycgczaza0pkhdxkjsl2lngcq0cj9zqr0rctk)#rrxd870r",
                        "hex": "0014b6c984605d175e1b5da6b4a1f57e68c01f891440",
                        "address": "bc1qkmycgczaza0pkhdxkjsl2lngcq0cj9zqr0rctk",
                        "type": "witness_v0_keyhash",
                    },
                },
            ],
            "fee": 0.000282,
            "hex": "01000000000101900a04350acfe8803603510ae22dd5a3a25dcc6434c20bd61a09247a664176890000000000ffffffff0230570500000000001600148548de2901afd06de6a6227b43cb2353994a0116211d060000000000160014b6c984605d175e1b5da6b4a1f57e68c01f89144002483045022100b069e6616dea0e0eac2cd0c9cb11e70ea0248903d07e786a2ee7b1fdfca1569d02205db21fcd1cf2357ca08f37ffed7a104f76aee89526510e1f567d713c3bcb732e012102b4ea32721b8f3b3a84816c246802508c02460189aaa28536b5e82cb120cf2beb00000000",
        },
    ],
}
