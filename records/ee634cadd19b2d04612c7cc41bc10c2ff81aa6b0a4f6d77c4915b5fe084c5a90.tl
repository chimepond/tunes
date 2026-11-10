# jottune-tl 1 1
# {
#   "collection": "Short-scene showcase",
#   "title": "Arc"
# }
# end-jottune-tl
timeline draft2
engine a1 sine_bits=12 exp_bits=10 phase64=1 env64=1 fb_avg=1 tables=58021f1150d3c8d2
fs 48000
length_ns 4000000000
space stereo
block 64
near 100
streams score
patch vowel 0 65536 3 0 0 0 12 700 50 180
patch vowel 1 65536 0 0 0 0 1 200 0 180
filter vowel lp 6000000 707
lfo vowel 0 4500 0 200 9 8 0 0 3 3 3
pd vowel 0 8192
formant vowel 0 700000 1800 0
formant vowel 1 1200000 2500 60
voice 0 vowel
hash 0b17706875b204a9
0 score 0 pos -82 997 0
0 score 0 on 195998 24000
350000000 score 0 off
540000000 score 0 pos 33 999 0
540000000 score 0 on 293665 24000
890000000 score 0 off
1080000000 score 0 pos -33 999 0
1080000000 score 0 on 233082 24000
1430000000 score 0 off
1620000000 score 0 pos 65 998 0
1620000000 score 0 on 329628 24000
1970000000 score 0 off
2160000000 score 0 pos -49 999 0
2160000000 score 0 on 220000 24000
2510000000 score 0 off
