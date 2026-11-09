function Shot_Normal( t )
{
	this.SetMotion(2009, 0);
	this.cancelCount = 1;
	this.atk_id = 16384;
	this.SetSpeed_XY(t.x * 0.80000001, t.y);
	this.rz = this.atan2(this.va.y, this.va.x * this.direction);
	this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
	local t_ = {};
	t_.rot <- this.rz;
	this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.Shot_Normal_Flash, t_);
	this.func = function ()
	{
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.Shot_HitSplash, {});
		this.ReleaseActor();
	};
	this.stateLabel = function ()
	{
		if (this.IsScreen(200) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return true;
		}

		if (this.hitCount > 0 || this.cancelCount <= 0 || this.grazeCount > 0)
		{
			this.func();
			return;
		}

		this.AddSpeed_XY(null, 0.20000000 * 0.80000001);
		this.count++;
		this.sx = this.sy = 1.00000000 + 0.20000000 * this.sin(this.count * 16 * 0.01745329);
		this.rz = this.atan2(this.vy, this.vx * this.direction);
		this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
	};
}

function Shot_Normal_Trail( t )
{
	this.SetMotion(2008, 0);
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.50000000 + this.rand() % 5 * 0.10000000;
	this.SetSpeed_XY(2 - this.rand() % 5, -1 - this.rand() % 3);
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.25000000);
		this.sx = this.sy += 0.05000000;
		this.alpha -= 0.05000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function Shot_Normal_Flash( t )
{
	this.rz = t.rot;
	this.SetMotion(2009, 1);
	this.stateLabel = function ()
	{
		this.sx = this.sy *= 0.89999998;
		this.alpha -= 0.25000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function Shot_HitSplash( t )
{
	this.SetMotion(2008, 0);
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.80000001 + this.rand() % 5 * 0.10000000;
	this.SetSpeed_XY(2 - this.rand() % 5, -1 - this.rand() % 3);
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.25000000);
		this.sx = this.sy += 0.05000000;
		this.alpha -= 0.05000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function Shot_Front( t )
{
	this.SetMotion(2019, 0);
	this.cancelCount = 1;
	this.atk_id = 65536;
	this.SetSpeed_Vec(t.vec, t.rot, this.direction);
	this.rz = t.rot;
	this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
	local t_ = {};
	t_.rot <- this.rz;
	this.SetFreeObject(this.x, this.y, this.direction, this.owner.Shot_FrontFlash, t_);
	this.func = function ()
	{
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.Shot_HitSplash, {});
		this.ReleaseActor();
	};
	this.stateLabel = function ()
	{
		if (this.IsScreen(200) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return;
		}

		if (this.hitCount > 0 || this.cancelCount <= 0 || this.grazeCount > 0)
		{
			this.func();
			return;
		}
	};
}

function Shot_FrontFlash( t )
{
	this.rz = t.rot;
	this.SetMotion(5010, 1);
	this.stateLabel = function ()
	{
		this.sx = this.sy *= 0.89999998;
		this.alpha -= 0.25000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function Shot_BarrageFire( t )
{
	this.SetMotion(2026, 3);
	this.stateLabel = function ()
	{
		this.sx = this.sy += 0.10000000;
		this.alpha = this.red -= 0.20000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function Shot_Barrage( t )
{
	this.SetMotion(2026, 0);
	this.SetFreeObject(this.x, this.y, this.direction, this.Shot_BarrageFire, {});
	this.rz = t.rot;
	this.SetSpeed_Vec(8.00000000, this.rz, this.direction);
	this.va.y *= 0.50000000;
	this.ConvertTotalSpeed();
	this.cancelCount = 1;
	this.atk_id = 262144;
	this.func = function ()
	{
		this.SetMotion(this.motion, 2);
		this.SetSpeed_XY(0.00000000, 0.00000000);
		this.stateLabel = function ()
		{
			this.sx = this.sy += (1.50000000 - this.sx) * 0.10000000;
			this.AddSpeed_XY(0.00000000, 0.50000000);
			this.alpha -= 0.10000000;

			if (this.alpha <= 0.00000000)
			{
				this.ReleaseActor();
			}
		};
	};
	this.subState = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.10000000);
		this.rz = this.atan2(this.va.y, this.va.x * this.direction);
	};
	this.stateLabel = function ()
	{
		if (this.IsScreen(100) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return true;
		}

		if (this.cancelCount <= 0 || this.hitCount > 0 || this.grazeCount > 0)
		{
			this.func();
			return true;
		}

		this.subState();
	};
}

function Shot_Charge( t )
{
	this.SetMotion(2029, 0);
	this.flag1 = 20;
	this.cancelCount = 3;
	this.atk_id = 131072;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;

	if (t.pare)
	{
		this.hitOwner = t.pare;
	}

	local t_ = {};
	t_.rot <- t.rot;
	this.SetFreeObject(this.x, this.y, this.direction, function ( t_ )
	{
		this.SetMotion(2029, 1);
		this.sx = this.sy = 0.50000000;
		this.rx = -t_.rot;
		this.ry = 30 * 0.01745329 * this.direction;
		this.rz = this.rand() % 360 * 0.01745329;
		this.stateLabel = function ()
		{
			this.rz -= 12.00000000 * 0.01745329;
			this.sx = this.sy += 0.15000001;
			this.rz -= 12.00000000 * 0.01745329;
			this.alpha = this.green = this.red -= 0.10000000;

			if (this.alpha <= 0.00000000)
			{
				this.ReleaseActor();
			}
		};
	}, t_);
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.SetCollisionRotation(0.00000000, 0.00000000, -this.rx);
	this.SetSpeed_Vec(15.00000000, -this.rx, this.direction);
	this.stateLabel = function ()
	{
		this.count++;
		this.rz -= 12.00000000 * 0.01745329;
		this.sx = this.sy += 0.05000000;
		this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
		this.alpha = this.green = this.red -= 0.01000000;

		if (this.count > this.flag1 || this.hitOwner.cancelCount <= 0 || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.SetMotion(this.motion, 1);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.05000000;
				this.rz -= 12.00000000 * 0.01745329;
				this.alpha = this.green = this.red -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	};
}

function Shot_ChargeB( t )
{
	this.SetMotion(2029, 2);
	this.flag1 = 20;
	this.cancelCount = 3;
	this.atk_id = 131072;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.SetCollisionRotation(0.00000000, 0.00000000, -this.rx);
	this.SetSpeed_Vec(15.00000000, -this.rx, this.direction);
	this.stateLabel = function ()
	{
		this.count++;
		this.rz -= 12.00000000 * 0.01745329;
		this.sx = this.sy += 0.10000000;
		this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
		this.alpha = this.green = this.red -= 0.01000000;

		if (this.count > this.flag1 || this.hitOwner.cancelCount <= 0 || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.SetMotion(this.motion, 3);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.10000000;
				this.rz -= 12.00000000 * 0.01745329;
				this.alpha = this.green = this.red -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	};
}

function Shot_ChargeFull( t )
{
	this.SetMotion(2028, 0);
	this.flag1 = 15;
	this.cancelCount = 3;
	this.atk_id = 131072;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	local t_ = {};
	t_.rot <- t.rot;
	this.SetFreeObject(this.x, this.y, this.direction, function ( t_ )
	{
		this.SetMotion(2029, 1);
		this.sx = this.sy = 0.50000000;
		this.rx = -t_.rot;
		this.ry = 30 * 0.01745329 * this.direction;
		this.rz = this.rand() % 360 * 0.01745329;
		this.stateLabel = function ()
		{
			this.rz -= 12.00000000 * 0.01745329;
			this.sx = this.sy += 0.15000001;
			this.rz -= 12.00000000 * 0.01745329;
			this.alpha = this.green = this.red -= 0.10000000;

			if (this.alpha <= 0.00000000)
			{
				this.ReleaseActor();
			}
		};
	}, t_);
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.SetCollisionRotation(0.00000000, 0.00000000, -this.rx);
	this.SetSpeed_Vec(25.00000000, -this.rx, this.direction);
	this.stateLabel = function ()
	{
		this.count++;
		this.rz -= 18.00000000 * 0.01745329;
		this.sx = this.sy += 0.10000000;
		this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
		this.alpha = this.green = this.red -= 0.01000000;

		if (this.count > this.flag1 || this.cancelCount <= 0)
		{
			this.SetMotion(this.motion, 1);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.05000000;
				this.rz -= 12.00000000 * 0.01745329;
				this.alpha = this.green = this.red -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	};
}

function Shot_ChargeFull_B( t )
{
	this.SetMotion(2028, 2);
	this.flag1 = 240;
	this.cancelCount = 6;
	this.atk_id = 131072;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 50 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.SetCollisionRotation(0.00000000, 0.00000000, -this.rx);
	this.SetSpeed_Vec(t.v, -this.rx, this.direction);
	this.stateLabel = function ()
	{
		this.count++;
		this.rz -= 12.00000000 * 0.01745329;
		this.Vec_Brake(1.25000000, t.v * 0.25000000);
		this.sx = this.sy += (1.75000000 - this.sx) * 0.05000000;
		this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
		this.HitCycleUpdate(10);

		if (this.count > this.flag1 || this.cancelCount <= 0 || this.hitCount >= 2 || this.grazeCount >= 10)
		{
			this.SetMotion(this.motion, 3);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.10000000;
				this.rz -= 12.00000000 * 0.01745329;
				this.alpha = this.green = this.red -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	};
}

function Occult_RoboBody( t )
{
	this.SetMotion(2509, 0);
	this.flag4 = null;
	this.flag5 = this.Vector3();
	this.DrawActorPriority(170);
	this.subState = function ()
	{
	};
	this.func = [
		function ()
		{
			this.ReleaseActor();
		},
		function ()
		{
			this.stateLabel = function ()
			{
				this.VX_Brake(0.50000000);
				this.stateLabel = function ()
				{
					this.AddSpeed_XY(0.00000000, this.va.y < 10.00000000 ? 0.50000000 : 0.01000000);

					if (this.y > ::battle.scroll_bottom + 500)
					{
						this.func[0].call(this);
					}
				};
			};
		},
		function ()
		{
			this.stateLabel = function ()
			{
				this.subState();
				this.flag5.x = (this.initTable.pare.x - 300 * this.direction - this.x) * 0.10000000;
				this.flag5.x = this.Math_MinMax(this.flag5.x, -10.00000000, 10.00000000);
				this.flag5.y = (::battle.scroll_bottom - this.y) * 0.10000000;
				this.flag5.y = this.Math_MinMax(this.flag5.y, -10.00000000, 10.00000000);
				this.SetSpeed_XY(this.flag5.x, this.flag5.y);
			};

			if (this.flag4)
			{
				this.flag4.func[0].call(this.flag4);
			}
		},
		function ()
		{
			this.flag4 = this.SetShot(this.x - 50 * this.direction, this.y - 200, this.direction, this.Occult_Steam, {}, this.weakref()).weakref();
			this.SetSpeed_XY(null, 0.00000000);
			this.count = 0;
			this.stateLabel = function ()
			{
				this.VX_Brake(0.75000000);
				this.count++;

				if (this.count >= 120)
				{
					if (this.flag4)
					{
						this.flag4.func[0].call(this.flag4);
					}
				}
			};
		}
	];
	this.func[2].call(this);
}

function Occult_Sprash( t )
{
	this.SetMotion(2508, 1);
	this.sy = 0.10000000;
	this.sx = 0.80000001 + this.rand() % 5 * 0.10000000;
	this.flag1 = this.Vector3();
	this.flag1.x = 0.02500000 + this.rand() % 25 * 0.00100000;
	this.flag1.y = 0.20000000 + this.rand() % 10 * 0.01000000;
	this.stateLabel = function ()
	{
		if (this.flag1.y > 0.00000000)
		{
			this.flag1.y -= 0.02000000;
		}
		else
		{
			this.flag1.y -= 0.00100000;
			this.alpha -= 0.05000000;
			this.red = this.green -= 0.02000000;

			if (this.alpha <= 0.00000000)
			{
				this.ReleaseActor();
				return;
			}
		}

		this.sx += this.flag1.x;
		this.sy += this.flag1.y;
	};
}

function Occult_NessyPod( t )
{
	this.SetMotion(2509, 5);
	this.DrawActorPriority(171);
	this.flag1 = this.Vector3();
	this.flag1.x = 1.00000000;
	this.func = [
		function ()
		{
			this.ReleaseActor();
		},
		function ()
		{
			this.PlaySE(2277);
			local t = {};
			t.rot <- this.rz;
			t.vec <- 32.50000000;
			this.SetShot(this.point0_x, this.point0_y, this.direction, this.Occult_Shot, t);
			this.team.op -= 20;

			if (this.team.op <= 0)
			{
				this.team.op = 0;
			}
		},
		function ()
		{
			this.SetMotion(2509, 4);
			this.stateLabel = function ()
			{
				this.x += (this.initTable.pare.point0_x - this.x) * 0.25000000;
				this.y += (this.initTable.pare.point0_y - this.y) * 0.25000000;
			};
		}
	];
	this.stateLabel = function ()
	{
		this.flag1 = this.TargetHoming_Vec(this.owner.target, this.flag1, 1.00000000 * 0.01745329, this.direction);
		this.rz = this.atan2(this.flag1.y, this.flag1.x);
		this.x += (this.initTable.pare.point0_x - this.x) * 0.25000000;
		this.y += (this.initTable.pare.point0_y - this.y) * 0.25000000;
		this.count++;
		local c_ = this.count % 120;

		if (c_ == 75 || c_ == 81 || c_ == 87 || c_ == 93 || c_ == 99)
		{
			this.func[1].call(this);

			if (this.team.op <= 0)
			{
				this.func[2].call(this);
				return;
			}
		}
	};
}

function Occult_Robo( t )
{
	this.SetMotion(2509, 0);
	this.flag2 = this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.Occult_NessyPod, {}, this.weakref()).weakref();
	this.flag5 = this.Vector3();
	this.func = [
		function ()
		{
			this.keyAction = null;
			this.owner.occult = 0;

			if (this.flag2)
			{
				this.flag2.func[0].call(this.flag2);
			}

			this.ReleaseActor();
		},
		function ()
		{
			if (this.flag2)
			{
				this.flag2.func[2].call(this.flag2);
			}

			this.keyAction = null;
			this.owner.occult = 2;
			this.SetMotion(2509, 1);
			this.count = 0;
			this.PlaySE(2275);

			if (this.team.op_stop < 300)
			{
				this.team.op_stop = 300;
				this.team.op_stop_max = 300;
			}

			this.stateLabel = function ()
			{
				this.count++;

				if (this.count % 6 == 1 && this.y < ::battle.scroll_bottom + 350)
				{
					this.SetFreeObject(this.x + (160 - this.rand() % 60) * this.direction, ::battle.scroll_bottom + this.rand() % 25, this.direction, this.Occult_Sprash, {});
				}

				this.AddSpeed_XY(0.25000000 * this.direction, this.va.y < 10.00000000 ? 0.50000000 : 0.01000000);

				if (this.y > ::battle.scroll_bottom + 500)
				{
					this.func[0].call(this);
				}
			};
		},
		function ()
		{
			this.keyAction = null;
			this.SetMotion(2509, 1);
			this.owner.occult = 1;
			this.count = 0;
			this.stateLabel = function ()
			{
				if (this.team.current.IsDamage())
				{
					this.func[1].call(this);
					return;
				}

				this.count++;
				this.subState();
				this.team.op_leak = 4;

				if (this.team.op - this.team.op_leak <= 0)
				{
					this.team.op = 0;
					this.func[1].call(this);
					return;
				}

				this.flag5.y = (::battle.scroll_bottom - this.y) * 0.10000000;
				this.flag5.y = this.Math_MinMax(this.flag5.y, -10.00000000, 10.00000000);
				this.SetSpeed_XY(this.flag5.x, this.flag5.y);
			};
		},
		function ()
		{
		},
		function ()
		{
			if (this.team.op <= 0)
			{
				this.team.op = 0;

				if (this.team.op_stop < 300)
				{
					this.team.op_stop = 300;
					this.team.op_stop_max = 300;
				}

				this.func[1].call(this);
				return;
			}

			this.SetMotion(2509, 2);
			this.PlaySE(2286);
			this.owner.occult = 1;
			this.SetSpeed_XY(0.00000000, 0.00000000);
			this.count = 0;
			this.keyAction = function ()
			{
				this.func[2].call(this);
			};
			this.stateLabel = function ()
			{
				if (this.team.current.IsDamage())
				{
					this.func[1].call(this);
					return;
				}

				if (this.team.op - this.team.op_leak <= 0)
				{
					this.team.op = 0;
					this.func[1].call(this);
					return;
				}

				this.team.op_leak = 4;
				this.count++;
				this.AddSpeed_XY(0.50000000 * this.direction, 0.00000000);

				if (this.x + 100 * this.direction < ::battle.scroll_left + 100 && this.va.x < 0.00000000 || this.x + 100 * this.direction > ::battle.scroll_right - 100 && this.va.x > 0.00000000)
				{
					this.SetSpeed_XY(0.00000000, null);
				}

				if (this.count >= 15)
				{
					this.stateLabel = function ()
					{
						this.team.op_leak = 4;

						if (this.team.current.IsDamage())
						{
							this.func[1].call(this);
							return;
						}

						if (this.team.op - this.team.op_leak <= 0)
						{
							this.team.op = 0;
							this.func[1].call(this);
							return;
						}

						this.VX_Brake(0.10000000);

						if (this.x + 100 * this.direction < ::battle.scroll_left + 100 && this.va.x < 0.00000000 || this.x + 100 * this.direction > ::battle.scroll_right - 100 && this.va.x > 0.00000000)
						{
							this.SetSpeed_XY(0.00000000, null);
						}
					};
				}
			};
		},
		function ()
		{
			this.SetMotion(2509, 2);
			this.PlaySE(2286);
			this.owner.occult = 1;
			this.SetSpeed_XY(0.00000000, 0.00000000);
			this.count = 0;
			this.keyAction = function ()
			{
				this.func[2].call(this);
			};
			this.stateLabel = function ()
			{
				if (this.team.current.IsDamage())
				{
					this.func[1].call(this);
					return;
				}

				if (this.team.op - this.team.op_leak <= 0)
				{
					this.team.op = 0;
					this.func[1].call(this);
					return;
				}

				this.team.op_leak = 4;
				this.count++;
				this.AddSpeed_XY(-0.50000000 * this.direction, 0.00000000);

				if (this.x + 100 * this.direction < ::battle.scroll_left + 100 && this.va.x < 0.00000000 || this.x + 100 * this.direction > ::battle.scroll_right - 100 && this.va.x > 0.00000000)
				{
					this.SetSpeed_XY(0.00000000, null);
				}

				if (this.count >= 15)
				{
					this.stateLabel = function ()
					{
						this.team.op_leak = 4;

						if (this.team.current.IsDamage())
						{
							this.func[1].call(this);
							return;
						}

						if (this.team.op - this.team.op_leak <= 0)
						{
							this.team.op = 0;
							this.func[1].call(this);
							return;
						}

						this.VX_Brake(0.10000000);

						if (this.x + 100 * this.direction < ::battle.scroll_left + 100 && this.va.x < 0.00000000 || this.x + 100 * this.direction > ::battle.scroll_right - 100 && this.va.x > 0.00000000)
						{
							this.SetSpeed_XY(0.00000000, null);
						}
					};
				}
			};
		},
		function ()
		{
			if (this.flag2)
			{
				this.flag2.func[2].call(this.flag2);
			}

			this.keyAction = null;
			this.owner.occult = 2;
			this.SetMotion(2509, 1);
			this.count = 0;
			this.PlaySE(2275);
			this.stateLabel = function ()
			{
				this.count++;

				if (this.count % 6 == 1 && this.y < ::battle.scroll_bottom + 350)
				{
					this.SetFreeObject(this.x + (160 - this.rand() % 60) * this.direction, ::battle.scroll_bottom + this.rand() % 25, this.direction, this.Occult_Sprash, {});
				}

				this.AddSpeed_XY(0.25000000 * this.direction, this.va.y < 10.00000000 ? 0.50000000 : 0.01000000);

				if (this.y > ::battle.scroll_bottom + 500)
				{
					this.func[0].call(this);
				}
			};
		}
	];
	this.DrawActorPriority(170);
	this.keyAction = function ()
	{
		this.func[2].call(this);
	};
	this.subState = function ()
	{
		if (this.y <= ::battle.scroll_bottom + 10)
		{
			this.owner.occult = 1;
			this.subState = function ()
			{
			};
		}
	};
	this.PlaySE(2275);
	this.stateLabel = function ()
	{
		if (this.team.current.IsDamage())
		{
			this.func[1].call(this);
			return;
		}

		this.team.op_leak = 4;
		this.subState();
		this.count++;

		if (this.count % 3 == 1 && this.y > ::battle.scroll_bottom + 100)
		{
			this.SetFreeObject(this.x + (160 - this.rand() % 60) * this.direction, ::battle.scroll_bottom + this.rand() % 25, this.direction, this.Occult_Sprash, {});
		}

		this.flag5.y = (::battle.scroll_bottom - this.y) * 0.10000000;
		this.flag5.y = this.Math_MinMax(this.flag5.y, -15.00000000, 12.50000000);
		this.SetSpeed_XY(this.flag5.x, this.flag5.y);
	};
}

function SearchLight( t )
{
	this.SetMotion(2508, 1);
	this.sx = this.sy = 0.50000000;
	this.alpha = 0.00000000;
	this.DrawActorPriority(t.priority);
	this.rz = t.rot;
	this.SetParent(t.pare, this.x - t.pare.x, this.y - t.pare.y);
	this.func = [
		function ()
		{
			this.stateLabel = function ()
			{
				this.alpha -= 0.05000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	];
	this.stateLabel = function ()
	{
		this.alpha += 0.05000000;

		if (this.alpha > 1.00000000)
		{
			this.alpha = 1.00000000;
		}

		this.sx = this.sy += (1.00000000 - this.sx) * 0.07500000;
	};
}

function Occult_ShotFire( t )
{
	this.SetMotion(2508, 3);
	this.DrawActorPriority(t.priority);
	this.keyAction = this.ReleaseActor;
	this.SetParent(t.pare, this.x - t.pare.x, this.y - t.pare.y);
}

function Occult_Shot( t )
{
	this.SetMotion(2508, 2);
	this.cancelCount = 1;
	this.atk_id = 524288;
	this.SetSpeed_Vec(t.vec, t.rot, this.direction);
	this.rz = t.rot;
	this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
	local t_ = {};
	t_.rot <- this.rz;
	this.SetFreeObject(this.x, this.y, this.direction, this.owner.Shot_FrontFlash, t_);
	this.func = function ()
	{
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.Shot_HitSplash, {});
		this.ReleaseActor();
	};
	this.stateLabel = function ()
	{
		if (this.IsScreen(200) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return;
		}

		if (this.hitCount > 0 || this.cancelCount <= 0 || this.grazeCount > 0)
		{
			this.func();
			return;
		}
	};
}

function Shot_ChangeFire( t )
{
	this.SetMotion(3929, 3 + this.rand() % 4);
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 1.75000000;
	this.stateLabel = function ()
	{
		this.sx = this.sy += 0.03000000;
		this.alpha -= 0.05000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function Shot_Change( t )
{
	this.SetMotion(3929, 0);
	this.SetFreeObject(this.x, this.y, this.direction, this.Shot_ChangeFire, {});
	this.rz = (-10 - this.rand() % 15) * 0.01745329;
	this.SetSpeed_Vec(20.00000000 - this.rand() % 6, this.rz, this.direction);
	this.cancelCount = 1;
	this.atk_id = 536870912;
	this.func = [
		function ()
		{
			this.SetMotion(this.motion, 2);
			this.stateLabel = function ()
			{
				this.alpha = this.red = this.green = this.blue -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		},
		function ()
		{
			this.PlaySE(2256);
			this.SetMotion(this.motion, 1);
			this.SetSpeed_XY(0.00000000, 0.00000000);
			this.stateLabel = function ()
			{
				this.sx = this.sy += (1.50000000 - this.sx) * 0.15000001;
				this.alpha = this.red -= 0.07500000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	];
	this.subState = function ()
	{
		this.VX_Brake(0.25000000);
		this.AddSpeed_XY(0.00000000, this.va.y < 0.00000000 ? 0.50000000 : 0.15000001);
		this.count++;

		if (this.count == 30)
		{
			this.TargetHoming(this.owner.target, 0.26179937, this.direction);
			this.subState = function ()
			{
				this.AddSpeed_Vec(0.44999999, null, 17.50000000, this.direction);
				this.TargetHoming(this.owner.target, 0.01745329, this.direction);
			};
		}
	};
	this.stateLabel = function ()
	{
		if (this.IsScreen(100) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return true;
		}

		this.subState();
		this.rz = this.atan2(this.va.y, this.va.x * this.direction);

		if (this.cancelCount <= 0 || this.hitCount > 0 || this.grazeCount > 0)
		{
			this.func[1].call(this);
			return true;
		}
	};
}

function SPShot_A( t )
{
	this.SetMotion(3009, 0);
	this.rz = -80.00000000 * 0.01745329;
	this.SetSpeed_Vec(35.00000000, this.rz, this.direction);
	this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
	this.cancelCount = 3;
	this.atk_id = 1048576;
	this.flag1 = this.Vector3();

	for( local i = 0; i < 4; i++ )
	{
		local t_ = {};
		t_.v <- 5 + i * 4 + this.rand() % 30 * 0.10000000;
		t_.rot <- this.rz + (10 - this.rand() % 20) * 0.01745329;
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_A_FireSmoke, t_);
	}

	for( local i = 0; i < 4; i++ )
	{
		local t_ = {};
		t_.v <- 5 + this.rand() % 80 * 0.10000000;
		t_.rot <- this.rz + (60 - this.rand() % 120) * 0.01745329;
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_A_FireSmoke, t_);
	}

	this.func = [
		function ()
		{
			local t_ = this.initTable;
			this.SetShot(this.x + this.va.x, this.y + this.va.y, this.direction, this.owner.SPShot_A_EXP, t_);

			if (this.flag3)
			{
				this.flag3.ReleaseActor();
			}

			this.ReleaseActor();
		},
		function ( pos_ )
		{
			this.PlaySE(2252);
			this.SetMotion(this.motion, 0);
			this.flag2 = pos_;
			this.rz = 80.00000000 * 0.01745329;
			this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
			this.SetSpeed_Vec(25.00000000, this.rz, this.direction);
			this.Warp(this.flag2.x - 800.00000000 * this.cos(this.rz) * this.direction, this.flag2.y - 800 * this.sin(this.rz));
			this.stateLabel = function ()
			{
				if (this.subState())
				{
					return;
				}

				this.flag1.x = this.flag2.x - this.x;
				this.flag1.y = this.flag2.y - this.y;

				if (this.flag1.Length() <= 100.00000000)
				{
					local t_ = this.initTable;
					this.SetShot(this.flag2.x, this.flag2.y, this.direction, this.owner.SPShot_A_EXP, t_);

					if (this.flag3)
					{
						this.flag3.ReleaseActor();
					}

					this.ReleaseActor();
				}
			};
		}
	];
	this.subState = function ()
	{
		if (this.Damage_ConvertOP(this.x, this.y, 8))
		{
			if (this.flag3)
			{
				this.flag3.ReleaseActor();
			}

			this.ReleaseActor();
			return true;
		}

		if (this.grazeCount > 0 || this.cancelCount <= 0 || this.hitCount > 0)
		{
			this.func[0].call(this);
			return true;
		}

		return false;
	};
	this.stateLabel = function ()
	{
		if (this.subState())
		{
			return;
		}

		this.count++;

		if (this.count >= 10 && this.y <= ::battle.scroll_top - 50.00000000)
		{
			if (this.initTable.fall)
			{
				local t_ = {};
				t_.scale <- this.initTable.scale;
				t_.x <- this.initTable.x;
				t_.y <- this.initTable.y;
				this.flag3 = this.SetShot(this.team.current.x + this.initTable.x * this.team.current.direction, this.team.current.y + this.initTable.y, this.team.current.direction, this.SPShot_A_Target, t_, this.weakref()).weakref();
				this.SetMotion(this.motion, 3);
				this.stateLabel = function ()
				{
				};
			}
			else
			{
				if (this.flag3)
				{
					this.flag3.ReleaseActor();
				}

				this.ReleaseActor();
			}
		}
	};
}

function SPShot_A_Fire( t )
{
	this.SetMotion(3009, t.type);
	this.rz = 0.17453292;
	this.FitBoxfromSprite();
	this.keyAction = this.ReleaseActor;
}

function SPShot_A_Target( t )
{
	this.SetMotion(3009, 2);
	this.flag1 = this.Vector3();
	this.sx = this.sy = t.scale;
	this.stateLabel = function ()
	{
		this.Warp(this.team.current.x + this.initTable.x * this.direction, this.team.current.y + this.initTable.y);
		this.initTable.x += this.team.current.va.x * 0.50000000 * this.team.current.direction;
		this.direction = this.team.current.direction;
		this.count++;

		if (this.GetTargetDist(this.owner.target) <= 10000.00000000 * this.sx)
		{
			this.PlaySE(2251);
			this.flag1.x = this.owner.target.x;
			this.flag1.y = this.owner.target.y;
			this.stateLabel = function ()
			{
				this.sx = this.sy += (1.00000000 - this.sx) * 0.20000000;
				this.rz += 6.00000000 * 0.01745329;
				this.Warp(this.x + (this.flag1.x - this.x) * 0.20000000, this.y + (this.flag1.y - this.y) * 0.20000000);
			};
			local pos_ = this.Vector3();
			pos_.x = this.flag1.x;
			pos_.y = this.flag1.y;
			this.initTable.pare.func[1].call(this.initTable.pare, pos_);
		}

		if (this.count >= 180 || this.owner.motion == 3000 && this.owner.keyTake == 0)
		{
			this.PlaySE(2251);
			this.stateLabel = function ()
			{
				this.sx = this.sy += (1.00000000 - this.sx) * 0.20000000;
				this.rz += 6.00000000 * 0.01745329;
			};
			local pos_ = this.Vector3();
			pos_.x = this.x;
			pos_.y = this.y;
			this.initTable.pare.func[1].call(this.initTable.pare, pos_);
		}
	};
}

function SPShot_A_TargetTrail( t )
{
	this.SetMotion(3009, 6);
	this.DrawActorPriority(180);
	this.stateLabel = function ()
	{
		this.alpha -= 0.07500000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SPShot_A_EXP( t )
{
	this.SetMotion(3009, 9);
	this.PlaySE(2253);
	this.keyAction = this.ReleaseActor;
	this.cancelCount = 10;
	this.atk_id = 1048576;
	this.stateLabel = function ()
	{
		this.count++;

		if (this.hitCount == 0)
		{
			this.HitCycleUpdate(0);
		}

		if (this.count >= 45 || this.cancelCount <= 0)
		{
			this.callbackGroup = 0;
			this.stateLabel = function ()
			{
				this.sx = this.sy *= 0.92000002;

				if (this.alpha <= 0.05000000)
				{
					this.ReleaseActor();
				}
				else
				{
					this.alpha -= 0.05000000;
				}
			};
		}
	};
}

function SPShot_A_Smoke( t )
{
	this.SetMotion(3008, 4);
	this.sx = this.sy = 0.80000001 + this.rand() % 20 * 0.01000000;
	this.SetSpeed_Vec(8.00000000, t.rot, this.direction);
	this.stateLabel = function ()
	{
		this.sx = this.sy += 0.01500000;
		this.alpha = this.green = this.red -= 0.07500000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}

		this.Vec_Brake(0.20000000);
	};
}

function SPShot_A_Splash( t )
{
	this.SetMotion(3009, 5);
	this.sx = this.sy = 0.80000001 + this.rand() % 20 * 0.01000000;
	this.rz = this.rand() % 360 * 0.01745329;
	this.SetSpeed_Vec(8.00000000, t.rot, this.direction);
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.50000000);
		this.sx = this.sy += 0.01500000;
		this.alpha = this.green = this.red -= 0.04000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SPShot_A_FireSmoke( t )
{
	this.DrawActorPriority(180);
	this.SetMotion(3008, this.rand() % 4);
	this.SetSpeed_Vec(t.v, t.rot, this.direction);
	this.sx = this.sy = 1.50000000 + this.rand() % 75 * 0.01000000;
	this.rz = this.rand() % 360 * 0.01745329;
	this.flag1 = 0.05000000 + this.rand() % 25 * 0.01000000;
	this.stateLabel = function ()
	{
		this.Vec_Brake(0.25000000, 1.00000000);
		this.sx = this.sy *= 0.98000002;
		this.alpha -= this.flag1;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SPShot_B( t )
{
	this.DrawActorPriority(180);

	if (this.sin(t.rot) < 0)
	{
		this.SetMotion(3019, 4);
	}
	else
	{
		this.SetMotion(3019, 0);
	}

	this.rz = t.rot;
	this.SetSpeed_Vec(2.00000000, this.rz, this.direction);
	this.life = 1;
	this.cancelCount = 1;
	this.atk_id = 2097152;
	this.stateLabel = function ()
	{
		if (this.rz >= 0.00000000)
		{
			this.AddSpeed_XY(null, 0.25000000);
		}
		else
		{
			this.AddSpeed_XY(null, -0.25000000);
		}

		this.rz = this.atan2(this.va.y, this.va.x * this.direction);
		this.count++;

		if (this.IsScreen(100) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return;
		}

		if (this.y >= ::battle.corner_bottom || this.grazeCount > 0 || this.hitCount > 0 || this.life <= 0 || this.cancelCount <= 0)
		{
			local t_ = {};
			t_.exp <- -1;

			if (this.va.y > 0.00000000)
			{
				t_.exp = 1;
			}

			this.SetShot(this.x, this.y, this.direction, this.owner.SPShot_B_EXP, t_);
			this.ReleaseActor();
			return;
		}
	};
}

function SPShot_B2( t )
{
	this.DrawActorPriority(180);
	this.SetMotion(3019, 0);
	this.rz = t.rot;
	this.SetSpeed_Vec(5.00000000, this.rz, this.direction);
	this.life = 1;
	this.cancelCount = 1;
	this.atk_id = 2097152;
	this.subState = function ()
	{
		if (this.IsScreen(100) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.ReleaseActor();
			return true;
		}

		if (this.y >= ::battle.corner_bottom || this.grazeCount > 0 || this.hitCount > 0 || this.life <= 0 || this.cancelCount <= 0)
		{
			this.SetShot(this.x, this.y, this.direction, this.owner.SPShot_B_EXP, {});
			this.ReleaseActor();
			return true;
		}

		return false;
	};
	this.stateLabel = function ()
	{
		if (this.subState())
		{
			return;
		}

		if (this.rz >= 0.00000000)
		{
			this.AddSpeed_XY(null, 0.25000000);
		}
		else
		{
			this.AddSpeed_XY(null, -0.25000000);
		}

		this.rz = this.atan2(this.va.y, this.va.x * this.direction);
		this.count++;

		if (this.count >= 15)
		{
			this.SetMotion(3019, 4);
			this.stateLabel = function ()
			{
				if (this.subState())
				{
					return;
				}

				this.rz *= 0.50000000;
				this.AddSpeed_XY(0.50000000 * this.direction, 0.00000000);
				this.VY_Brake(2.00000000);
			};
		}
	};
}

function SPShot_B_EXP( t )
{
	this.SetMotion(3019, 1);
	this.PlaySE(2256);
	this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_B_Ring, {});
	this.cancelCount = 1;
	this.atk_id = 2097152;
	this.stateLabel = function ()
	{
		this.count++;

		if (this.hitCount == 0)
		{
			this.HitCycleUpdate(1);
		}

		if (this.count >= 5)
		{
			this.callbackGroup = 0;
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.05000000;

				if (this.alpha <= 0.05000000)
				{
					this.ReleaseActor();
				}
				else
				{
					this.alpha -= 0.05000000;
				}
			};
		}
	};
}

function SPShot_B_Ring( t )
{
	this.SetMotion(3019, 3);
	this.stateLabel = function ()
	{
		this.sx = this.sy += 0.10000000;

		if (this.alpha <= 0.05000000)
		{
			this.ReleaseActor();
		}
		else
		{
			this.alpha = this.red = this.green -= 0.05000000;
		}
	};
}

function SPShot_C( t )
{
	this.DrawActorPriority(170);
	this.SetMotion(3029, 0);
	this.flag1 = {};
	this.flag1.x <- this.x;
	this.flag1.y <- this.y;
	this.rz = t.rot;
	this.SetSpeed_Vec(t.vec, t.rot, this.direction);
	local t_ = {};
	t_.rot <- this.rz;
	this.Warp(this.owner.point0_x + this.x - this.point0_x, this.owner.point0_y + this.y - this.point0_y);
	this.flag2 = this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_C_Wire, t_).weakref();
	this.flag2.flag1.x = this.point0_x + this.va.x;
	this.flag2.flag1.y = this.point0_y + this.va.y;
	this.subState = function ()
	{
		local c_ = true;

		if (this.owner.motion >= 3020 && this.owner.motion <= 3022)
		{
			if (this.owner.keyTake == 0 || this.owner.keyTake >= 4)
			{
				c_ = false;
			}
		}
		else
		{
			c_ = false;
		}

		if (!c_)
		{
			this.ReleaseActor.call(this.flag2);
			this.ReleaseActor();
			return true;
		}
		else
		{
			this.flag2.flag1.x = this.point0_x + this.va.x;
			this.flag2.flag1.y = this.point0_y + this.va.y;
			return false;
		}
	};
	this.stateLabel = function ()
	{
		if (this.subState())
		{
			return;
		}

		this.count++;

		if (this.count >= 3)
		{
			if (this.initTable.rockCount <= this.count || this.x > ::battle.corner_right && this.va.x > 0.00000000 || this.x < ::battle.corner_left && this.va.x < 0.00000000 || this.y > ::battle.corner_bottom && this.va.y > 0.00000000 || this.y < ::battle.corner_top && this.va.y < 0.00000000)
			{
				this.PlaySE(2258);
				this.owner.flag2 = true;
				this.SetMotion(3029, 1);

				if (!(this.initTable.rockCount <= this.count))
				{
					this.callbackGroup = 0;
				}

				this.SetSpeed_XY(0.00000000, 0.00000000);
				this.rz = this.atan2(this.owner.y - this.y, (this.owner.x - this.x) * this.direction) + 3.14159203;
				this.stateLabel = function ()
				{
					if (this.subState())
					{
						return;
					}
				};
			}
		}
	};
}

function SPShot_C2( t )
{
	this.DrawActorPriority(170);
	this.SetMotion(3029, 0);
	this.flag1 = this.Vector3();
	this.flag1.x = this.x;
	this.flag1.y = this.y;
	this.rz = t.rot;
	this.SetSpeed_Vec(t.vec, t.rot, this.direction);
	local t_ = {};
	t_.rot <- this.rz;
	this.Warp(this.owner.point0_x + this.x - this.point0_x, this.owner.point0_y + this.y - this.point0_y);
	this.flag2 = this.SetShot(this.point0_x, this.point0_y, this.direction, this.owner.SPShot_C_Wire, t_).weakref();
	this.linkObject = [
		this.flag2.weakref()
	];
	this.flag2.flag1.x = this.point0_x + this.va.x;
	this.flag2.flag1.y = this.point0_y + this.va.y;
	this.flag3 = 0;
	this.subState = function ()
	{
		local c_ = true;

		if (this.owner.motion == 3021)
		{
			if (this.owner.keyTake == 0 || this.owner.keyTake >= 6)
			{
				c_ = false;
			}
		}
		else
		{
			c_ = false;
		}

		this.rz = this.flag2.rz;

		if (!c_)
		{
			this.ReleaseActor();
			return true;
		}
		else
		{
			this.flag2.flag1.x = this.point0_x + this.va.x;
			this.flag2.flag1.y = this.point0_y + this.va.y;
		}

		return false;
	};
	this.stateLabel = function ()
	{
		if (this.subState())
		{
			return;
		}

		this.count++;
		this.flag3 += this.abs(this.va.x);

		if (this.flag3 >= 250)
		{
			this.SetMotion(3029, 1);
			this.callbackGroup = 0;
			this.owner.flag2 = true;
			this.SetSpeed_XY(0.00000000, 0.00000000);
			this.PlaySE(2258);
			this.stateLabel = function ()
			{
				if (this.subState())
				{
					return;
				}
			};
		}
	};
}

function SPShot_C_Wire( t )
{
	this.DrawActorPriority(170);
	this.SetMotion(3029, 2);
	this.rz = t.rot;
	this.sx = 0.00000000;
	this.flag1 = this.Vector3();
	this.flag2 = this.Vector3();
	this.Warp(this.owner.point0_x, this.owner.point0_y);
	this.linkObject = [
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_C_Point, {}).weakref()
	];
	this.stateLabel = function ()
	{
		this.Warp(this.owner.point0_x, this.owner.point0_y);
		this.flag2.x = this.flag1.x - this.x;
		this.flag2.y = this.flag1.y - this.y;

		if (this.flag2.x == 0.00000000 && this.flag2.y == 0.00000000)
		{
			this.sx = 0.00000000;
		}
		else
		{
			this.rz = this.atan2(this.flag2.y, this.flag2.x * this.direction);
			this.sx = this.abs(this.flag2.LengthXY()) / 64.00000000;
		}
	};
}

function SPShot_C_Point( t )
{
	this.DrawActorPriority(170);
	this.SetMotion(3029, 3);
	this.Warp(this.owner.point0_x, this.owner.point0_y);
	this.stateLabel = function ()
	{
		this.Warp(this.owner.point0_x, this.owner.point0_y);
	};
}

function SPShot_E( t )
{
	this.SetMotion(3049, t.keyTake);
	this.SetSpeed_Vec(t.v, t.rot, this.direction);
	this.life = 1;
	this.cancelCount = 3;
	this.atk_id = 8388608;
	this.FitRotatefromVec();
	this.func = function ()
	{
		this.PlaySE(2267);
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.owner.SPShot_E_Exp, {});
		this.ReleaseActor();
	};
	this.subState = function ()
	{
		if (this.hitResult == 32)
		{
			this.HitReset();
		}

		if (this.hitCount > 0 || this.cancelCount <= 0)
		{
			this.func();
			return true;
		}

		return false;
	};
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.25000000);
		this.FitRotatefromVec();
		this.FitBoxfromSprite();

		if (this.subState())
		{
			return;
		}

		if (this.direction == 1.00000000 && this.point0_x >= ::battle.corner_right || this.direction == -1.00000000 && this.point0_x <= ::battle.corner_left)
		{
			for( local i = 0; i < 6; i++ )
			{
				local t_ = {};
				t_.v <- 10.00000000 + this.rand() % 30 * 0.10000000;
				t_.rot <- (30 - i * 12 + this.rand() % 10) * 0.01745329;
				this.SetShot(this.point0_x, this.point0_y, -this.direction, this.owner.SPShot_E_Buble, t_);
			}

			this.func();
			return;
		}

		if (this.IsScreen(300.00000000) || this.Damage_ConvertOP(this.x, this.y, 8))
		{
			this.ReleaseActor();
			return;
		}
	};
}

function SPShot_E2( t )
{
	this.SetMotion(3048, 3);
	this.SetSpeed_Vec(t.v, t.rot, this.direction);
	this.life = 1;
	this.cancelCount = 3;
	this.atk_id = 8388608;
	this.FitRotatefromVec();
	this.func = function ()
	{
		this.PlaySE(2267);
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.owner.SPShot_E_Exp, {});
		this.ReleaseActor();
	};
	this.subState = function ()
	{
		if (this.hitResult == 32)
		{
			this.HitReset();
		}

		if (this.hitCount > 0 || this.cancelCount <= 0 || this.count >= 5)
		{
			this.func();
			return true;
		}

		return false;
	};
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.25000000);
		this.FitRotatefromVec();
		this.FitBoxfromSprite();

		if (this.subState())
		{
			return;
		}

		this.count++;

		if (this.direction == 1.00000000 && this.point0_x >= ::battle.corner_right || this.direction == -1.00000000 && this.point0_x <= ::battle.corner_left)
		{
			this.func();
			return;
		}

		if (this.IsScreen(300.00000000))
		{
			this.ReleaseActor();
			return;
		}
	};
}

function SPShot_E_Exp( t )
{
	this.SetMotion(3049, 1);
	this.keyAction = this.ReleaseActor;
}

function SPShot_E_Buble( t )
{
	this.SetMotion(3049, 2);
	this.SetSpeed_Vec(t.v, t.rot, this.direction);
	this.sx = this.sy = 0.50000000 + this.rand() % 25 * 0.01000000;
	local sp_ = 0.80000001 + this.rand() % 7 * 0.10000000;
	this.SetSpeed_XY(this.va.x * sp_, null);
	this.FitBoxfromSprite();
	this.cancelCount = 1;
	this.func = function ()
	{
		this.callbackGroup = 0;
		this.stateLabel = function ()
		{
			this.SetSpeed_XY(this.va.x * 0.80000001, this.va.y * 0.80000001);
			this.sx = this.sy += 0.10000000;
			this.alpha -= 0.10000000;

			if (this.alpha <= 0.00000000)
			{
				this.ReleaseActor();
			}
		};
	};
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.30000001);
		this.VX_Brake(0.10000000);

		if (this.hitCount > 0 || this.grazeCount > 0 || this.cancelCount <= 0 || this.IsScreen(250.00000000) || this.Damage_ConvertOP(this.x, this.y, 1))
		{
			this.func();
			return;
		}
	};
}

function SPShot_F( t )
{
	this.SetMotion(3059, 0);
	this.SetSpeed_XY(t.x * this.direction, t.y);
	this.flag1 = this.y;
	local rot = this.atan2(this.va.y, this.va.x * this.direction);

	for( local i = 0; i < 4; i++ )
	{
		local t_ = {};
		t_.v <- 5 + i * 4 + this.rand() % 30 * 0.10000000;
		t_.rot <- rot + (10 - this.rand() % 20) * 0.01745329;
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_FireSmoke, t_);
	}

	for( local i = 0; i < 4; i++ )
	{
		local t_ = {};
		t_.v <- 5 + this.rand() % 80 * 0.10000000;
		t_.rot <- rot + (60 - this.rand() % 120) * 0.01745329;
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_FireSmoke, t_);
	}

	if (!t.fire)
	{
		this.ReleaseActor();
		return;
	}

	this.func = function ()
	{
		local t_ = {};
		t_.rot <- -90.00000000 * 0.01745329;

		if (this.initTable.exp)
		{
			this.SetShot(this.x, this.y, this.direction, this.owner.SPShot_F_Exp, t_);
		}
		else
		{
			this.SetShot(this.x, this.y, this.direction, this.owner.SPShot_F_ExpB, t_);
		}

		this.ReleaseActor();
	};
	this.stateLabel = function ()
	{
		this.rz += 18.00000000 * 0.01745329;
		this.AddSpeed_XY(0.00000000, 0.75000000);
		this.VX_Brake(0.25000000);
		this.count++;

		if (this.va.y > 0 && this.y > this.flag1 + 30)
		{
			this.func();
			return;
		}

		if (this.IsScreen(100.00000000) || this.Damage_ConvertOP(this.x, this.y, 8))
		{
			this.ReleaseActor();
			return;
		}
	};
}

function SPShot_F_Exp( t )
{
	this.PlaySE(2270);
	this.rz = t.rot;
	this.SetCollisionRotation(0.00000000, 0.00000000, this.rz);
	this.sy = 2.00000000;
	this.SetMotion(3058, 0);
	this.cancelCount = 3;
	this.atk_id = 16777216;
	this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_Exp2, {});

	for( local i = 0; i < 8; i++ )
	{
		local t_ = {};
		t_.v <- 5.00000000 + i * 1.25000000 + this.rand() % 20 * 0.10000000;
		t_.rot <- this.rz;
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_Splush, t_);
	}

	for( local i = 0; i < 8; i++ )
	{
		local t_ = {};
		t_.v <- 5.00000000 + i * 1.25000000 + this.rand() % 20 * 0.10000000;
		t_.rot <- this.rz + 3.14159203;
		this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_Splush, t_);
	}

	this.option = [
		null,
		null
	];
	local t_ = {};
	t_.type <- 1;
	t_.rot <- 90 * 0.01745329;
	t_.exp <- true;
	this.option[0] = this.SetShot(this.x, this.y, this.direction, this.owner.SPShot_F_ExpBox, t_).weakref();
	this.option[0].hitOwner = this.weakref();
	local t_ = {};
	t_.type <- -1;
	t_.rot <- -90 * 0.01745329;
	t_.exp <- true;
	this.option[1] = this.SetShot(this.x, this.y, this.direction, this.owner.SPShot_F_ExpBox, t_).weakref();
	this.option[1].hitOwner = this.weakref();
	this.func = [
		function ()
		{
			if (this.option[0])
			{
				this.option[0].ReleaseActor();
			}

			if (this.option[1])
			{
				this.option[1].ReleaseActor();
			}

			this.callbackGroup = 0;
			this.stateLabel = function ()
			{
				this.sx += (2.00000000 - this.sx) * 0.20000000;
				this.sy *= 0.99000001;
				this.alpha = this.green = this.red -= 0.04000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	];
	this.stateLabel = function ()
	{
		if (this.hitCount < 4)
		{
			this.HitCycleUpdate(10);
		}

		if (this.count >= 40 || this.hitCount >= 4 || this.cancelCount <= 0 || this.owner.motion == 3050 && this.owner.keyTake == 0 || this.Damage_ConvertOP(this.x, this.y, 8))
		{
			this.func[0].call(this);
			return;
		}

		this.count++;

		if (this.count % 4 == 0)
		{
			local t_ = {};
			t_.v <- (10.00000000 + this.rand() % 50 * 0.10000000) * this.sx;
			t_.rot <- this.rz;
			this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_Splush, t_);
		}
		else
		{
			local t_ = {};
			t_.v <- (10.00000000 + this.rand() % 50 * 0.10000000) * this.sx;
			t_.rot <- this.rz + 3.14159203;
			this.SetFreeObject(this.x, this.y, this.direction, this.owner.SPShot_F_Splush, t_);
		}

		this.sx += (3.00000000 - this.sx) * 0.20000000;
		this.sy *= 0.99000001;

		foreach( a in this.option )
		{
			if (a != this)
			{
				a.sx = this.sx;
				a.sy = this.sy;
			}
		}
	};
}

function SPShot_F_ExpBox( t )
{
	this.rz = t.rot;

	if (t.exp)
	{
		if (t.type == 1)
		{
			this.SetMotion(3056, 0);
		}
		else
		{
			this.SetMotion(3057, 0);
		}
	}
	else if (t.type == 1)
	{
		this.SetMotion(3056, 2);
	}
	else
	{
		this.SetMotion(3057, 2);
	}

	this.cancelCount = 3;
	this.atk_id = 16777216;
	this.isVisible = false;
	this.FitBoxfromSprite();
	this.stateLabel = function ()
	{
		this.FitBoxfromSprite();
	};
}

function SPShot_F_Exp2( t )
{
	this.SetMotion(3058, 2);
	this.rz = 0.01745329 * this.rand() % 360;
	this.stateLabel = function ()
	{
		this.sx = this.sy += (3.00000000 - this.sx) * 0.10000000;
		this.alpha = this.green = this.red -= 0.07500000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SPShot_F_Splush( t )
{
	this.SetMotion(3058, 3);
	this.rz = 0.01745329 * this.rand() % 360;
	this.sx = this.sy = 0.75000000 + this.rand() % 6 * 0.10000000;
	this.SetSpeed_Vec(t.v, t.rot + (10 - this.rand() % 20) * 0.01745329, this.direction);
	this.stateLabel = function ()
	{
		this.Vec_Brake(0.75000000, 1.00000000);
		this.sx = this.sy += 0.07500000;
		this.alpha = this.green = this.red -= 0.05000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SPShot_F_FireSmoke( t )
{
	this.SetMotion(3008, this.rand() % 4);
	this.SetSpeed_Vec(t.v, t.rot, this.direction);
	this.sx = this.sy = 1.50000000 + this.rand() % 75 * 0.01000000;
	this.rz = this.rand() % 360 * 0.01745329;
	this.flag1 = 0.05000000 + this.rand() % 25 * 0.01000000;
	this.stateLabel = function ()
	{
		this.Vec_Brake(0.25000000, 1.00000000);
		this.sx = this.sy *= 0.98000002;
		this.alpha -= this.flag1;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SPShot_G( t )
{
	if (t.fuel)
	{
		this.SetMotion(6060, 0);
		this.flag1 = 15;
		this.cancelCount = 3;
	}
	else
	{
		this.SetMotion(6061, 0);
		this.flag1 = 10;
		this.cancelCount = 1;
	}

	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	local t_ = {};
	t_.rot <- t.rot;
	this.SetFreeObject(this.x, this.y, this.direction, function ( t_ )
	{
		this.SetMotion(6060, 1);
		this.sx = this.sy = 0.50000000;
		this.rx = -t_.rot;
		this.ry = 30 * 0.01745329 * this.direction;
		this.rz = this.rand() % 360 * 0.01745329;
		this.stateLabel = function ()
		{
			this.rz -= 12.00000000 * 0.01745329;
			this.sx = this.sy += 0.15000001;
			this.rz -= 12.00000000 * 0.01745329;
			this.alpha = this.green = this.red -= 0.10000000;

			if (this.alpha <= 0.00000000)
			{
				this.ReleaseActor();
			}
		};
	}, t_);
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.SetCollisionRotation(0.00000000, 0.00000000, -this.rx);
	this.SetSpeed_Vec(15.00000000, -this.rx, this.direction);
	this.stateLabel = function ()
	{
		this.count++;
		this.rz -= 12.00000000 * 0.01745329;
		this.sx = this.sy += 0.05000000;
		this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
		this.alpha = this.green = this.red -= 0.01000000;

		if (this.count > this.flag1 || this.cancelCount <= 0)
		{
			this.SetMotion(this.motion, 1);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.05000000;
				this.rz -= 12.00000000 * 0.01745329;
				this.alpha = this.green = this.red -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	};
}

function SPShot_G2( t )
{
	if (t.fuel)
	{
		this.SetMotion(6060, 2);
		this.flag1 = 15;
		this.cancelCount = 3;
	}
	else
	{
		this.SetMotion(6061, 2);
		this.flag1 = 10;
		this.cancelCount = 1;
	}

	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.50000000;
	this.rx = -t.rot;
	this.ry = 30 * 0.01745329 * this.direction;
	this.rz = this.rand() % 360 * 0.01745329;
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.SetCollisionRotation(0.00000000, 0.00000000, -this.rx);
	this.SetSpeed_Vec(15.00000000, -this.rx, this.direction);
	this.stateLabel = function ()
	{
		this.count++;
		this.rz -= 12.00000000 * 0.01745329;
		this.sx = this.sy += 0.10000000;
		this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
		this.alpha = this.green = this.red -= 0.01000000;

		if (this.count > this.flag1 || this.cancelCount <= 0)
		{
			this.SetMotion(this.motion, 3);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.10000000;
				this.rz -= 12.00000000 * 0.01745329;
				this.alpha = this.green = this.red -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}
	};
}

function SpellShot_A( t )
{
	this.SetMotion(4009, 1);
	this.SetSpeed_Vec(15.00000000, -45.00000000 * 0.01745329, this.direction);
	this.DrawActorPriority(180);
	this.cancelCount = 3;
	this.atk_id = 67108864;
	this.EnableTimeStop(false);
	this.func = [
		function ()
		{
			this.SetSpeed_XY(0.00000000, 0.00000000);
			this.count = 0;
			this.PlaySE(2302);
			this.stateLabel = function ()
			{
				this.count++;

				if (this.count < 10)
				{
					::camera.shake_radius = 2.00000000;
				}

				if (this.count == 10)
				{
					::camera.shake_radius = 5.00000000;
					local t_ = {};
					t_.y <- 1280;
					t_.rate <- this.initTable.rate;
					this.flag1 = this.SetShotDynamic(this.direction == 1.00000000 ? 0 : 1280, 1280, this.direction, this.owner.SpellShot_A_Wave, t_).weakref();
				}

				if (this.count <= 480)
				{
					if (this.count % 6 == 1)
					{
						this.SetFreeObject(this.rand() % 1280, ::battle.scroll_top - 300.00000000, this.direction, this.owner.SpellShot_A_Rain, {});
					}

					if (this.count % 4 == 1)
					{
						this.SetFreeObject(this.rand() % 1280, ::battle.scroll_top - 300.00000000, this.direction, this.owner.SpellShot_A_Rain, {});
					}
				}

				if (this.flag1)
				{
					if (this.count == 240 || this.flag1.hitCount > 11)
					{
						this.team.spell_enable_end = true;
						this.flag1.func();
						this.ReleaseActor();
					}
				}
			};
		}
	];
	this.SetMotion(4009, 0);
	::camera.shake_radius = 2.00000000;
	this.stateLabel = function ()
	{
		this.AddSpeed_Vec(1.00000000, null, 45.00000000, this.direction);
		this.count++;
		local t_ = {};
		t_.rot <- 135 * 0.01745329;
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, function ( t_ )
		{
			this.EnableTimeStop(false);
			this.SetMotion(4009, 2);
			this.DrawActorPriority(170);
			this.sx = this.sy = 1.25000000 + this.rand() % 20 * 0.01000000;
			this.SetSpeed_Vec(15.00000000 + this.rand() % 2, t_.rot, this.direction);
			this.stateLabel = function ()
			{
				this.Vec_Brake(0.75000000, 1.00000000);
				this.sx = this.sy += 0.10000000;
				this.alpha -= 0.10000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}, t_);
		local t_ = {};
		t_.rot <- (130 + this.rand() % 11) * 0.01745329;
		this.SetFreeObject(this.point0_x, this.point0_y, this.direction, function ( t_ )
		{
			this.EnableTimeStop(false);
			this.SetMotion(4009, 5 + this.rand() % 4);
			this.DrawActorPriority(170);
			this.alpha = 0.00000000;
			this.sx = this.sy = 1.25000000 + this.rand() % 20 * 0.01000000;
			this.SetSpeed_Vec(15.00000000 + this.rand() % 2, t_.rot, this.direction);
			this.subState = function ()
			{
				this.alpha += 0.04000000;

				if (this.alpha >= 0.50000000)
				{
					this.subState = function ()
					{
						this.alpha -= 0.02000000;
					};
				}
			};
			this.stateLabel = function ()
			{
				this.Vec_Brake(0.75000000, 2.00000000);
				this.sx = this.sy += 0.01000000;
				this.subState();

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
		}, t_);

		if (this.count >= 30)
		{
			this.func[0].call(this);
			return;
		}
	};
}

function SpellShot_A_Rain( t )
{
	this.SetMotion(4009, 3 + this.rand() % 2);
	this.EnableTimeStop(false);
	this.sx = this.sy = 1.00000000 + this.rand() % 50 * 0.01000000;
	this.alpha = 0.40000001 + this.rand() % 7 * 0.10000000;
	this.SetSpeed_Vec(80.00000000 + this.rand() % 20, 80 * 0.01745329, this.direction);
	this.stateLabel = function ()
	{
		if (this.y >= ::battle.scroll_bottom + 300.00000000)
		{
			this.ReleaseActor();
		}
	};
}

function SpellShot_A_Wave( t )
{
	this.sx = 2.00000000;
	this.sy = 1.50000000;
	this.atkRate_Pat = t.rate;
	this.cancelCount = 99;
	this.atk_id = 67108864;
	this.DrawActorPriority(210);
	this.SetMotion(4008, 0);
	this.PlaySE(2303);
	this.func = function ()
	{
		if (this.flag2)
		{
			this.flag2.func();
			this.flag2 = null;
		}

		this.count = 0;
		this.stateLabel = function ()
		{
			this.anime.left -= 30.00000000 * this.direction;
			this.count++;
			this.AddSpeed_XY(0.00000000, 0.10000000);

			if (this.count >= 300)
			{
				this.ReleaseActor();
				return;
			}
		};
	};
	this.stateLabel = function ()
	{
		this.anime.left -= 30.00000000 * this.direction;
		this.count++;
		this.HitCycleUpdate(15);
		this.flag1 = 50 * this.sin(this.count * 3 * 0.01745329);
		this.initTable.y += (800 - this.initTable.y) * 0.10000000;
		this.Warp(this.x, this.initTable.y + this.flag1);
	};
	local t_ = {};
	t_.y <- 1400;
	this.flag2 = this.SetShotDynamic(this.direction == 1.00000000 ? 0 : 1280, 1280, this.direction, this.owner.SpellShot_A_WaveB, t_).weakref();
}

function SpellShot_A_WaveB( t )
{
	this.sx = 2.00000000;
	this.sy = 1.00000000;
	this.DrawActorPriority(180);
	this.SetMotion(4008, 1);
	this.func = function ()
	{
		this.count = 0;
		this.team.spell_enable_end = true;
		this.stateLabel = function ()
		{
			this.anime.left -= 14.00000000 * this.direction;
			this.count++;
			this.AddSpeed_XY(0.00000000, 0.10000000);

			if (this.count >= 300)
			{
				this.ReleaseActor();
				return;
			}
		};
	};
	this.stateLabel = function ()
	{
		this.anime.left -= 14.00000000 * this.direction;
		this.count++;
		this.flag1 = 50 * this.sin(this.count * 1.29999995 * 0.01745329);
		this.initTable.y += (650 - this.initTable.y) * 0.10000000;
		this.Warp(this.x, this.initTable.y + this.flag1);
	};
}

function SpellShot_B( t )
{
	this.SetMotion(4019, 0);
	this.sx = this.sy = 0.10000000;
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.flag1 = 0.00000000;
	this.flag2 = 0.00000000;
	this.flag5 = {};
	this.flag5.scale <- this.Vector3();
	this.flag5.bubble <- 0;
	this.atkRate_Pat = t.rate;
	this.cancelCount = 3;
	this.atk_id = 67108864;
	this.func = [
		function ( s_ )
		{
			this.team.spell_enable_end = true;
			this.PlaySE(2307);

			if (this.flag5.bubble > 0)
			{
				for( local i = 0; i < 360; i = i + this.flag5.bubble )
				{
					local t_ = {};
					t_.rot <- (i + this.rand() % 10) * 0.01745329;
					t_.rate <- this.atkRate_Pat;
					t_.scale <- s_;
					this.SetShot(this.point0_x + this.rand() % 100 * this.direction * this.cos(t_.rot), this.point0_y + this.rand() % 100 * this.sin(t_.rot), this.direction, this.owner.SpellShot_B_Small, t_);
				}
			}

			this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.owner.SpellShot_B_Hit, {});
			this.ReleaseActor();
		},
		function ()
		{
			this.SetMotion(4019, 1);
			this.HitReset();
			this.AddSpeed_XY(8.00000000 * this.direction, 0.00000000);
			this.flag3 = this.y;
			this.flag5.scale.x = this.sx;
			this.flag5.scale.y = this.sy;
			this.flag5.bubble = 15;
			this.sx *= 0.89999998;
			this.sy *= 1.10000002;
			this.keyAction = function ()
			{
				this.stateLabel = function ()
				{
					if (this.va.x > 0 && this.x > ::battle.scroll_right || this.va.x < 0 && this.x < ::battle.scroll_left)
					{
						this.ReleaseActor();
						return;
					}

					this.HitCycleUpdate(10);

					if (this.hitCount > 0 || this.cancelCount <= 0 || this.count >= 150)
					{
						this.func[0].call(this, 1.00000000);
						return;
					}

					this.sx += (this.flag5.scale.x - this.sx) * 0.10000000;
					this.sy += (this.flag5.scale.y - this.sy) * 0.10000000;
					this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
					this.Vec_Brake(0.15000001, 1.00000000);
					this.count++;

					if (this.count >= 30)
					{
						this.flag2 += (100 - this.flag2) * 0.01000000;
						this.flag4 += 1.50000000 * 0.01745329;
						this.Warp(this.x, this.flag3 + this.flag2 * this.sin(this.flag4));
					}
				};
			};
			this.stateLabel = function ()
			{
				if (this.hitResult & 1 && this.owner.target.team.life > 0 && ::battle.state == 8)
				{
					this.SetMotion(4019, 4);
					this.count = 0;
					this.owner.target.freeMap = true;
					this.owner.target.DamageGrab_Common(301, 0, -this.direction);
					this.owner.target.freeMap = true;
					this.owner.target.Warp(this.point0_x, this.point0_y);
					this.stateLabel = function ()
					{
						this.sx += (this.flag5.scale.x - this.sx) * 0.10000000;
						this.sy += (this.flag5.scale.y - this.sy) * 0.10000000;
						this.count++;

						if (this.direction == 1.00000000 && this.point0_x > ::battle.corner_right - 100)
						{
							if (this.va.x > 0.00000000)
							{
								this.VX_Brake(1.00000000);
							}
							else
							{
								this.AddSpeed_XY(-0.20000000, null);

								if (this.va.x < -3.00000000)
								{
									this.SetSpeed_XY(-3.00000000, null);
								}
							}
						}
						else if (this.direction == -1.00000000 && this.point0_x < ::battle.corner_left + 100)
						{
							if (this.va.x < 0.00000000)
							{
								this.VX_Brake(1.00000000);
							}
							else
							{
								this.AddSpeed_XY(0.20000000, null);

								if (this.va.x > 3.00000000)
								{
									this.SetSpeed_XY(3.00000000, null);
								}
							}
						}
						else
						{
							this.VX_Brake(0.10000000);
						}

						this.flag2 += (40 - this.flag2) * 0.10000000;
						this.flag4 += 1.50000000 * 0.01745329;
						this.Warp(this.x, this.flag3 + this.flag2 * this.sin(this.flag4));
						this.owner.target.Warp(this.point0_x, this.point0_y);

						if (this.count % 30 == 1)
						{
							this.PlaySE(2315);
							this.target = this.owner.target.weakref();
							this.owner.target.DamageGrab_Common(201, 1, -this.direction);
							this.owner.KnockBackTarget.call(this, -this.direction);
						}

						if (this.count >= 150 || this.owner.target.damageTarget != this || ::battle.state != 8)
						{
							this.func[3].call(this);
							return;
						}
					};
				}
			};
		},
		function ()
		{
			this.SetMotion(4019, 2);
			this.AddSpeed_XY(8.00000000 * this.direction, 0.00000000);
			this.flag3 = this.y;
			this.flag5.scale.x = this.sx;
			this.flag5.scale.y = this.sy;
			this.sx *= 0.89999998;
			this.sy *= 1.10000002;
			this.flag5.bubble = 45;
			this.stateLabel = function ()
			{
				this.HitCycleUpdate(10);

				if (this.hitCount > 0 || this.cancelCount <= 0)
				{
					this.func[0].call(this, this.sx);
					return;
				}

				this.sx += (this.flag5.scale.x - this.sx) * 0.10000000;
				this.sy += (this.flag5.scale.y - this.sy) * 0.10000000;
				this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
				this.Vec_Brake(0.15000001, 1.00000000);
				this.count++;

				if (this.count >= 30)
				{
					this.flag2 += (100 - this.flag2) * 0.01000000;
					this.flag4 += 1.50000000 * 0.01745329;
					this.Warp(this.x, this.flag3 + this.flag2 * this.sin(this.flag4));
				}
			};
		},
		function ()
		{
			::camera.shake_radius = 3.00000000;
			this.PlaySE(2307);
			this.SetMotion(4019, 5);
			this.SetEffect(this.point0_x, this.point0_y, this.direction, this.EF_HitSmashC, {});
			this.target = this.owner.target.weakref();
			this.owner.KnockBackTarget.call(this, -this.direction);
			this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.owner.SpellShot_B_Hit, {});
			this.ReleaseActor();
		}
	];
	this.stateLabel = function ()
	{
		if (this.owner.motion == 4010)
		{
			this.sx = this.sy += 0.02200000;
			this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
			this.Warp(this.owner.point0_x, this.owner.point0_y);
			this.HitCycleUpdate(5);
		}
		else
		{
			this.flag5.bubble = 15;
			this.func[0].call(this, 1.00000000);
		}
	};
}

function SpellShot_B_Hit( t )
{
	this.PlaySE(2307);
	this.SetMotion(4019, 3);
	this.flag1 = 0.40000001;
	this.sx = this.sy = 1.50000000;
	this.stateLabel = function ()
	{
		this.sx = this.sy += this.flag1;
		this.flag1 *= 0.80000001;
		this.alpha -= 0.20000000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
			return;
		}
	};
}

function SpellShot_B_Small( t )
{
	this.SetMotion(4018, 0);
	this.atkRate_Pat = t.rate;
	this.sx = this.sy = (0.75000000 + this.rand() % 26 * 0.01000000) * t.scale;
	this.SetCollisionScaling(this.sx, this.sy, 1.00000000);
	this.flag1 = 4.00000000 + this.rand() % 20 * 0.10000000;
	this.cancelCount = 1;
	this.atk_id = 67108864;
	this.SetSpeed_Vec(15 + this.rand() % 30 * 0.10000000, t.rot, this.direction);
	this.flag2 = 120 + this.rand() % 35;
	this.stateLabel = function ()
	{
		this.flag2--;

		if (this.life < 0 || this.hitTarget.len() > 0 || this.cancelCount <= 0 || this.grazeCount >= 1 || this.flag2 <= 0)
		{
			this.SetMotion(4018, 1);
			this.SetSpeed_XY(0.00000000, 0.00000000);
			this.stateLabel = function ()
			{
				this.sx = this.sy += 0.10000000;
				this.alpha -= 0.20000000;

				if (this.alpha <= 0.00000000)
				{
					this.ReleaseActor();
				}
			};
			return;
		}

		if (this.va.x > 0.00000000 && this.x >= ::battle.scroll_right || this.va.x < 0.00000000 && this.x <= ::battle.scroll_left)
		{
			this.SetSpeed_XY(-this.va.x, null);
		}

		if (this.va.y > 0.00000000 && this.y >= ::battle.scroll_bottom || this.va.y < 0.00000000 && this.y <= ::battle.scroll_top)
		{
			this.SetSpeed_XY(null, -this.va.y);
		}

		this.Vec_Brake(0.60000002, this.flag1);
	};
}

function SpellShot_C_Wing( t )
{
	this.EnableTimeStop(false);
	this.SetMotion(4029, 0);
	this.stateLabel = function ()
	{
		this.count++;

		if (this.count >= 300 || this.owner.motion != 4020)
		{
			this.ReleaseActor();
			return;
		}

		if (this.abs(this.owner.target.x - this.x) <= 100.00000000 || (this.x - this.owner.x) * this.direction >= 0.00000000)
		{
			this.owner.func();
			this.ReleaseActor();
			return;
		}

		this.SetSpeed_XY(40.00000000 * this.direction, (this.owner.y + 30 - this.y) * 0.20000000);
	};
}

function SpellShot_C_WingB( t )
{
	this.DrawActorPriority(180);
	this.SetMotion(4029, 0);
	this.SetSpeed_XY(t.vx, t.vy);
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, -0.25000000);

		if (this.IsScreen(300))
		{
			this.ReleaseActor();
		}
	};
}

function SpellC_Fire( t )
{
	this.DrawActorPriority(180);
	this.rz = -5.00000000 * 0.01745329;

	if (t.type == 0)
	{
		this.SetMotion(4029, 1);
	}
	else
	{
		this.SetMotion(4029, 2);
	}

	if (t.point == 0)
	{
		this.flag1 = "point0_";
	}
	else
	{
		this.flag1 = "point1_";
	}

	this.func = function ()
	{
		this.subState = function ()
		{
			this.rz = this.atan2(this.owner.va.y, this.owner.va.x * this.direction);
			this.sx += (20 - this.sx) * 0.10000000;
			this.sy += (4.00000000 - this.sy) * 0.10000000;
		};
	};
	this.stateLabel = function ()
	{
		if (this.owner.motion == 4020)
		{
			if (this.owner.keyTake <= 5)
			{
				if (this.subState)
				{
					this.subState();
				}

				this.Warp(this.owner[this.flag1 + "x"], this.owner[this.flag1 + "y"]);
			}
			else
			{
				this.stateLabel = function ()
				{
					this.alpha = this.green = this.red -= 0.03500000;

					if (this.alpha <= 0.00000000)
					{
						this.ReleaseActor();
					}
				};
			}
		}
		else
		{
			this.ReleaseActor();
		}
	};
}

function SpellShot_C_Flash( t )
{
	this.EnableTimeStop(false);
	this.SetMotion(4028, 0);
	this.keyAction = this.ReleaseActor;
}

function Climax_Mask( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.SetMotion(4908, 0);
	this.DrawActorPriority(180);
	this.anime.is_write = true;
	this.isVisible = false;
	this.stateLabel = function ()
	{
		this.Warp(this.owner.x, this.y + (this.owner.y - this.y) * 0.1);
	};
}

function Climax_MaskFlash( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.SetMotion(7904, 1);
	this.stateLabel = function ()
	{
		this.Warp(this.owner.x, this.owner.y);
		this.alpha -= 0.075;
		if (this.alpha <= 0.0)
		{
			this.ReleaseActor();
		}
	};
}

function Climax_Cut( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.target = this.owner.target.weakref();
	this.SetMotion(7901, 0);
	this.DrawScreenActorPriority(100);
	this.flag1 = this.Vector3();
	this.flag1.x = 640 - 640 * this.direction;
	this.flag2 = 0;
	this.flag3 = 0;
	this.flag4 = this.Vector3();
	this.flag5 = 0;
	this.subState = function ()
	{
	};
	this.stateLabel = function ()
	{
		this.count++;
		this.flag1.x = 640 - ((640 + 80 * this.sin(this.count * 2 * 0.01745329)) * this.direction);
		this.flag1.y = 40 * this.sin(this.count * 3 * 0.01745329);
		this.rz = 2.0 * 0.01745329 * this.sin(this.count * 1.5 * 0.01745329);
		this.Warp(this.x + (this.flag1.x - this.x) * 0.025, this.y + (this.flag1.y - this.y) * 0.025);
		this.subState();
		this.flag2--;
		if (this.flag2 == 0)
		{
			this.PlaySE(2355);
		}
		if (this.flag2 < -60)
		{
			this.flag2 = -60;
		}
		if (this.owner.input.b0 == 1 && this.flag2 <= 0)
		{
			this.flag2 += 60;
			this.flag5++;
			this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.owner.Climax_Fire, {});
			local t_ = {};
			t_.pos <- this.Vector3();
			t_.pos.x = this.owner.x;
			t_.pos.y = this.owner.y;
			t_.motion <- 7902;
			if (this.flag5 % 4 == 0)
			{
				t_.motion = 7903;
			}
			this.SetFreeObject(this.point0_x, this.point0_y, this.direction, this.owner.Climax_Bullet, t_);
			this.flag3 = 9;
			this.subState = function ()
			{
				this.Warp(this.x - this.flag3 * this.direction, this.y + this.flag3 * 3);
				this.flag3 -= 1;
				if (this.flag3 <= 0)
				{
					this.subState = function ()
					{
					};
				}
			};
		}
		if (this.count >= 600 || this.target.team.life <= 0 || 99 == 0)
		{
			if (this.count < 600)
			{
				this.count = 600;
			}
			this.PlaySE(2357);
			this.owner.SetMotion(4900, 4);
			this.SetSpeed_XY.call(this.owner, 0.0, 0.0);
			this.owner.stateLabel = null;
			this.stateLabel = function ()
			{
				this.flag4.x -= 0.2;
				this.flag4.y -= 0.75;
				this.flag1.x += this.flag4.x * this.direction;
				this.flag1.y += this.flag4.y;
				this.Warp(this.x + (this.flag1.x - this.x) * 0.025, this.y + (this.flag1.y - this.y) * 0.025);
				this.count++;
				if (this.count >= 720)
				{
					this.owner.Spell_Climax_End(null);
					this.ReleaseActor();
				}
			};
		}
	};
}

function Climax_Fire( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.rz = 10 * 0.01745329;
	this.SetMotion(7901, 1);
	this.DrawScreenActorPriority(90);
	this.keyAction = this.ReleaseActor;
	this.PlaySE(2353);
}

function Climax_Bullet( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.SetMotion(t.motion, 0);
	this.sy = 2.0;
	this.sx = 2.0;
	this.DrawScreenActorPriority(80);
	this.SetSpeed_Vec(35.0, -70 * 0.01745329, this.direction);
	this.stateLabel = function ()
	{
		this.sy -= 0.02;
		this.sx = this.sy;
		this.count++;
		if (this.count >= 30)
		{
			this.SetFreeObject(640, -1280, this.direction, this.owner.Climax_FallBullet, this.initTable);
			this.ReleaseActor();
		}
	};
}

function Climax_FallBullet( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.SetMotion(t.motion, 0);
	this.flag1 = (80.0 + this.rand() % 20) * 0.01745329;
	this.SetSpeed_Vec(20.0, this.flag1, this.direction);
	this.Warp(this.initTable.pos.x - 800.0 * this.cos(this.flag1) * this.direction, this.initTable.pos.y - 800 * this.sin(this.flag1));
	this.stateLabel = function ()
	{
		this.sy -= 0.02;
		this.sx = this.sy;
		if (((this.x - this.initTable.pos.x) * (this.x - this.initTable.pos.x) + (this.y - this.initTable.pos.y) * (this.y - this.initTable.pos.y)) <= 2500.0)
		{
			if (this.motion == 7902)
			{
				this.SetShot(this.initTable.pos.x, this.initTable.pos.y, this.direction, this.owner.Climax_Exp, {});
			}
			else
			{
				this.SetShot(this.initTable.pos.x, this.initTable.pos.y, this.direction, this.owner.Climax_BigExp, {});
			}
			this.ReleaseActor();
		}
	};
}

function Climax_Exp( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.SetMotion(7902, 1);
	this.keyAction = this.ReleaseActor;
	this.PlaySE(2354);
}

function Climax_BigExp( t )
{
	while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
	{
		this.owner = this.owner.owner;
	}
	this.SetMotion(7903, 1);
	::camera.Shake(2.0);
	this.keyAction = this.ReleaseActor;
	this.PlaySE(2354);
}

function WinA_FireCore( t )
{
	this.SetMotion(9010, 4);
	this.DrawActorPriority(180);
	this.SetSpeed_XY(2.00000000 - this.rand() % 5, -15.00000000 - this.rand() % 5);
	this.stateLabel = function ()
	{
		this.AddSpeed_XY(0.00000000, 0.10000000);
		this.sx = this.sy *= 0.92000002;

		if (this.sx < 0.01000000)
		{
			this.ReleaseActor();
		}
	};
}

function WinA_FireWork( t )
{
	this.SetMotion(9010, 5);
	this.keyAction = this.ReleaseActor;
	this.sx = this.sy = 0.89999998 + this.rand() % 3 * 0.10000000;
}

function LoseA_SmokeA( t )
{
	this.SetMotion(9020, 3 + this.rand() % 4);
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.20000000 + this.rand() % 10 * 0.01000000;
	this.SetSpeed_Vec(35.00000000, this.rand() % 360 * 0.01745329, this.direction);
	this.DrawActorPriority(180);
	this.stateLabel = function ()
	{
		this.SetSpeed_XY(this.va.x * 0.80000001, this.va.y * 0.80000001);
		this.sx = this.sy *= 0.92000002;

		if (this.sx <= 0.01000000)
		{
			this.ReleaseActor();
		}
	};
}

function LoseA_SmokeB( t )
{
	this.SetMotion(9020, 3 + this.rand() % 4);
	this.rz = this.rand() % 360 * 0.01745329;
	this.sx = this.sy = 0.89999998 + this.rand() % 20 * 0.01000000;
	this.DrawActorPriority(180);
	this.stateLabel = function ()
	{
		this.sx = this.sy += 0.01000000;
		this.alpha -= 0.02500000;

		if (this.alpha <= 0.00000000)
		{
			this.ReleaseActor();
		}
	};
}

