{
  "codecaves": {
    "th135_vfs_open": {
      "access": "xr",
      "code": "ff7508 e8[stack_game_file_stream] 83c404 83f8ff 7401 c3 59 6a00 6a00 6a03 6a00 6a03 6800000080 ff7508 83c102 ffe1"
    }
  },
  "binhacks": {
    "th135_vfs_patch_first_1": {
      "title": "Check thcrap raw patch files before pak archive (step 1)",
      "addr": "Rx23e71",
      "expected": "85db0f85bf000000",
      "code": "9090909090909090"
    },
    "th135_vfs_patch_first_2": {
      "title": "Fallback router to pak archive when not in raw patch (step 2)",
      "addr": "Rx23e81",
      "expected": "8bd78d4a018a024284c075f98b450c2bd152578bc8e88522000084c00f848a000000",
      "code": "eb20 85db 0f85ad000000 e99d000000 90909090909090909090909090909090909090"
    },
    "th135_vfs_patch_first_3": {
      "title": "Route loose open miss to pak fallback router (step 3)",
      "addr": "Rx23ead",
      "expected": "0f847a000000",
      "code": "0f84d0ffffff"
    },
    "th135_vfs_stream_hook": {
      "title": "Open raw mod assets directly from thcrap patch stack via stack_game_file_stream",
      "addr": "Rx2de94",
      "expected": "6a006a006a036a006a03680000008050",
      "code": "e8[codecave:th135_vfs_open] 750f 909090909090909090"
    },
    "vec3transform_fix": {
      "title": "Fix Manbow Matrix::Vec3Transform register bug in TH15.5 (0x004422A6: 8D 45 F4 -> 8B 45 08)",
      "addr": "Rx422a6",
      "expected": "8d45f4",
      "code": "8b4508"
    }
  }
}