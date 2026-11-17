if (!("background3d_mob" in ::graphics.slot))
{
	::manbow.graphics.CreateCommandSlot("background3d_mob");
	::manbow.graphics.CompileFile("data/script/renderer_vm.nut");
	::manbow.graphics.SetRenderFunction("Render");
	local _orig_show_bg = ::graphics.ShowBackground;
	::graphics.ShowBackground <- function(t)
	{
		_orig_show_bg.call(this, t);
		this.slot.background3d_mob.Show(t);
	};
}

this.actor_mob <- [];
this.mob <- {};

this.SetMob <- function(point, name_, pat_ = null, dir = null)
{
	if (pat_ == null)
	{
		pat_ = name_;
	}

	if (dir == null)
	{
		dir = "data/background/mob/";
	}

	local t = {};
	t.animation_set2d <- ::manbow.AnimationSet2D();
	t.animation_set2d.Load(dir + pat_ + ".pat", null);
	t.actor <- ::manbow.AnimationController2D();
	t.actor.Init(t.animation_set2d);
	t.actor.SetMotion(0, 0);

	if (name_ in this.mob)
	{
		if (!("child" in this.mob[name_]))
		{
			this.mob[name_].child <- [];
			this.mob[name_].SetWorldTransform <- function(mat)
			{
				this.actor.SetWorldTransform(mat);
				this.actor.Update();

				foreach (a in this.child)
				{
					a.actor.SetWorldTransform(mat);
					a.actor.Update();
				}
			};
		}

		this.mob[name_].child.append(t);
	}
	else
	{
		t.matrix <- ::manbow.Matrix();
		point.GetMeshNodeMatrix("00" + name_, t.matrix);
		t.mat_local <- ::manbow.Matrix();
		t.mat_local.SetIdentity();
		t.pos <- ::manbow.Vector3();
		t.pos.x = 0;
		t.pos.y = 0;
		t.pos.z = 0;
		t.SetWorldTransform <- function(mat)
		{
			this.actor.SetWorldTransform(mat);
			this.actor.Update();
		};
		this.mob[name_] <- t;
		t.Update <- function()
		{
		};
	}

	t.actor.ConnectRenderSlot(::graphics.slot.background2d_0, 100);
	this.actor_mob.append(t);
	return t;
};

this.UpdateMob <- function()
{
	local mat_view = ::manbow.Matrix();
	local mat = ::manbow.Matrix();
	local pos = ::manbow.Vector3();
	::camera.camera2d.GetViewProjectionMatrix(mat);
	mat.Inverse();
	::camera.camera3d.GetViewProjectionMatrix(mat_view);
	mat_view.Multiply(mat);

	foreach (a in this.mob)
	{
		a.Update();
		mat.Set(a.matrix);
		pos.Set(a.pos);
		mat.Multiply(mat_view);
		mat.Vec3Transform(pos);
		mat.Set(a.mat_local);
		mat.Translate(pos.x, pos.y, 0);
		a.SetWorldTransform(mat);
	}
};

this.SetUpdateFunctionType0 <- function(name, _scale, interval, max_y)
{
	local t = this.mob[name];
	t.count <- 0;
	t._scale <- _scale;
	t.interval <- interval;
	t.max_y <- max_y;
	t.Update <- function()
	{
		this.count++;
		this.mat_local.SetScaling(this._scale, this._scale, 1.0);
		this.mat_local.Translate(0, sin(this.count * this.interval) * this.max_y, 0);
	};
};

this.SetUpdateFunctionType1 <- function(name, _scale, interval, max_y, offset)
{
	local t = this.mob[name];
	t.count <- offset;
	t._scale <- _scale;
	t.interval <- interval;
	t.max_y <- max_y;
	t.Update <- function()
	{
		this.count++;
		this.mat_local.SetScaling(this._scale, this._scale, 1.0);
		this.mat_local.Translate(sin(this.count * this.interval * 1.2345) * this.max_y, sin(this.count * this.interval) * this.max_y, sin(this.count * this.interval * 2.345) * this.max_y);
	};
};
