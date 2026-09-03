import pstats

p = pstats.Stats("var/profiles/cpu_profile_20260831T202306Z.prof")
# p.print_callers("dumps")
p.print_callers("sqltypes.py:2798")
