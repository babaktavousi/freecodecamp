"""Verbatim excerpt (a few lines) of the publicly available European Technical Assessment ETA-16/0143
(Hilti HIT-RE 500 V3, CSTB, 14/05/2019), kept ONLY as a parser test fixture.  Lines are reproduced exactly as
they come out of PDF text extraction - including the letter-spaced ETA number, decimal commas and the lost tau glyph."""

PAGES = [
"""European Technical
Assessment
ETA-1 6 / 0 1 4 3
du 14/05/2019
Nom commercial:
Trade name:
Injection system Hilti HIT-RE 500 V3
Basis of ETA:
EAD 330499-01-0601
Seismic performance category C1
Seismic performance category C2 (HAS-U, HAS-U-F, HIT-V , HIT-V-F, AM, AM-HDG grade 8.8 and""",
"""Table B1: Installation parameters of threaded rod, HAS-U- HIT-V- and AM 8.8
Threaded rod, HAS-U- , HIT-V- M8 M10 M12 M16 M20 M24 M27 M30
Diameter of element d [mm] 8 10 12 16 20 24 27 30
Nominal diameter of drill bit d0 [mm] 10 12 14 18 22 28 30 35
Effective embedment depth and
drill hole depth hef [mm]
60
to
160
60
to
200
70
to
240
80
to
320
90
to
400
96
to
480
108
to
540
120
to
600
Maximum diameter of clearance
hole in the fixture df [mm] 9 12 14 18 22 26 30 33
Minimum thickness of concrete
member hmin [mm] hef + 30 hef + 2 d0 Maximum torque Tmax [Nm] 10 20 40 80 150 200 270 300
Minimum spacing smin [mm] 40 50 60 75 90 115 120 140
Minimum edge distance cmin [mm] 40 45 45 50 55 60 75 80""",
"""Table C1: Essential characteristics for threaded rods under tension load in concrete
Threaded rod, HAS-U- , HIT-V- , AM M8 M10 M12 M16 M20 M24 M27 M30
Installation factor
Hammer drilling inst [-] 1,0
Hammer drilling with
Hilti hollow drill bit TE-CD or TE-YD inst [-] - 1,0
Diamond coring inst [-] 1,2 1,4
Hammer drilling in water-filled drill holes inst [-] 1,4
Concrete cone failure
Factor for cracked concrete kcr,N [-] 7,7
Factor for uncracked concrete kucr,N [-] 11,0
Edge distance ccr,N [mm] 1,5 hef Spacing scr,N [mm] 3,0 hef Splitting failure
Edge distance
ccr,sp [mm] for
h / hef 1,0 hef 2,0 > h / hef > 1,3 4,6 hef - 1,8 h
h / hef 2,26 hef Spacing scr,sp [mm] 2 ccr,sp""",
"""Table C1: continued
Threaded rod, HAS-U- , HIT-V- M8 M10 M12 M16 M20 M24 M27 M30
Combined pullout and concrete cone failure for a service life of 50 years
Uncracked concrete C20/25
in hammer drilled holes and hammer drilled holes with Hilti hollow drill bit TE-CD or TE-YD
and diamond cored holes with roughening with Hilti roughening tool TE-YRT
Temperature range I: 40°C / 24°C Rk,ucr [N/mm2] 19 18 18 17 16 15 15 14
Temperature range II: 70°C / 43°C Rk,ucr [N/mm2] 14 14 14 13 12 12 11 11
Uncracked concrete C20/25
in diamond cored holes.
Temperature range I: 40°C / 24°C Rk,ucr [N/mm2] 13 13 13 13 12 12 12 12
Temperature range II: 70°C / 43°C Rk,ucr [N/mm2] 10 9,5 9,5 9,5 9 9 9 9
Uncracked concrete C20/25
in hammer drilled holes and installation in water-filled drill holes
Temperature range I: 40°C / 24°C Rk,ucr [N/mm2] 16 16 15 15 14 13 12 12
Temperature range II: 70°C / 43°C Rk,ucr [N/mm2] 12 12 12 11 10 10 9,5 9,5
Cracked concrete C20/25
in hammer drilled holes and hammer drilled holes with Hilti hollow drill bit TE-CD or TE-YD
and diamond cored holes with roughening with Hilti roughening tool TE-YRT
Temperature range I: 40°C / 24°C Rk,cr [N/mm2] 7,5 8 9,5 9,5 9,5 8,5 9 8,5
Temperature range II: 70°C / 43°C Rk,cr [N/mm2] 6 7 7,5 7,5 7,5 7 7 6,5
Influence factors on bond resistance Rk Influence of concrete strength
Factor for
concrete
compressive
strength
in hammer drilled holes
c C30/37 1,04
C40/50 1,07
C50/60 1,09
Influence of sustained load
Sustained
load factor
0
sus 40°C / 24°C 0,88
70°C / 43°C 0,70""",
"""Table C1: continued
Threaded rod, HAS-U- , HIT-V- M8 M10 M12 M16 M20 M24 M27 M30
Combined pullout and concrete cone failure for a service life of 100 years
Uncracked concrete C20/25
in hammer drilled holes and hammer drilled holes with Hilti hollow drill bit TE-CD or TE-YD
Temperature range I: 40°C / 24°C Rk,ucr [N/mm2] 19 18 18 17 16 15 15 14
Cracked concrete C20/25
in hammer drilled holes and hammer drilled holes with Hilti hollow drill bit TE-CD or TE-YD
Temperature range I: 40°C / 24°C Rk,cr [N/mm2] 5,5 6,5 7 6,5 6,5 6 6 5,5""",
"""Table C2: Essential characteristics for internally threaded sleeve HIS-(R)N under tension load in concrete
Internally threaded sleeve HIS-(R)N M8 M10 M12 M16 M20
Combined pullout and concrete cone failure for a service life of 50 years
Uncracked concrete C20/25
in hammer drilled holes
Temperature range I: 40°C / 24°C Rk,ucr [N/mm2] 14 14 14 14 14""",
"""Table C4: Essential characteristics for reinforcing bars (rebars) under tension load in concrete
Reinforcing bar (rebar) 8 10 12 14 16 20 25 28 30 32
Steel failure
Rebar B500B acc. to DIN 488:2009-08 2) NRk,s [kN] 28 43 62 85 111 173 270 339 388 442
Combined pullout and concrete cone failure for a service life of 50 years
Uncracked concrete C20/25
in hammer drilled holes and hammer drilled holes with Hilti hollow drill bit TE-CD or TE-YD
Temperature range I: 40°C / 24°C Rk,ucr [N/mm2] 10 15 15 15 15 14 13 13 13 13
Cracked concrete C20/25
in hammer drilled holes and hammer drilled holes with Hilti hollow drill bit TE-CD or TE-YD
Temperature range I: 40°C / 24°C Rk,cr [N/mm2] 5 8,5 9,5 9,5 10 10 10 11 11 11""",
]
