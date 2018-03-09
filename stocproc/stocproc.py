"""
Stochastic Process Generators
=============================


Karhunen-Loève expansion
------------------------

.. toctree::
   :maxdepth: 2

   StocProc_KLE

This method samples stochastic processes using Karhunen-Loève expansion and
is implemented in the class :doc:`StocProc_KLE </StocProc_KLE>`.

Setting up the class involves solving an eigenvalue problem which grows with
the time interval the process is simulated on. Further generating a new process
involves a multiplication with that matrix, therefore it scales quadratically with the
time interval. Nonetheless it turns out that this method requires less random numbers
than the Fast-Fourier method.


Fast-Fourier method
-------------------

.. toctree::
   :maxdepth: 2

   StocProc_FFT

In the class :doc:`StocProc_FFT </StocProc_FFT>` a method based on Fast-Fourier transform is
used to sample stochastic processes.

Setting up this class is quite efficient as it only calculates values of the
associated spectral density. The number scales linear with the time interval of interest. However to achieve
sufficient accuracy many of these values are required. As the generation of a new process is based on
a Fast-Fouried-Transform over these values, this part is comparably lengthy.
"""
import abc
import numpy as np
import time

from . import method_kle
from . import method_fft
import fcSpline

import logging
log = logging.getLogger(__name__)

class _absStocProc(abc.ABC):
    r"""
    Abstract base class to stochastic process interface
    
    general work flow:
        - Specify the time axis of interest [0, t_max] and it resolution (number of grid points), :math:`t_i = i \frac{t_max}{N_t-1}.  
        - To evaluate the stochastic process at these points, a mapping from :math:`N_z` normal distributed 
          random complex numbers with :math:`\langle y_i y_j^\ast \rangle = 2 \delta_{ij}`
          to the stochastic process :math:`z_{t_i}` is needed and depends on the implemented method (:py:func:`_calc_z').
        - A new process should be generated by calling :py:func:`new_process'.
        - When the __call__ method is invoked the results will be interpolated between the :math:`z_t_i`.
        
      
    """
    def __init__(self, t_max=None, num_grid_points=None, seed=None, t_axis=None, scale=1):
        r"""
            :param t_max: specify time axis as [0, t_max], if None, the times must be explicitly
                given by t_axis
            :param num_grid_points: number of equidistant times on that axis
            :param seed: if not ``None`` set seed to ``seed``
            :param t_axis: an explicit definition of times t_k (may be non equidistant)
        """
        if t_max is not None:
            self.t = np.linspace(0, t_max, num_grid_points)
        else:
            self.t = t_axis

        self.t_max = self.t[-1]
        self.num_grid_points = len(self.t)

        self._z = None
        self._interpolator = None
        self._seed = seed
        if seed is not None:
            np.random.seed(seed)
        self._one_over_sqrt_2 = 1/np.sqrt(2)
        self._proc_cnt = 0
        self.scale = scale
        self.sqrt_scale = np.sqrt(self.scale)
        log.debug("init StocProc with t_max {} and {} grid points".format(t_max, num_grid_points))

    def __call__(self, t=None):
        r"""evaluates the stochastic process via spline interpolation of the discrete process :math:`z_k`

        :param t: time to evaluate the stochastic process at, float of array of floats, if t is None
            return the discrete process :math:`z_k` which corresponds to the times :math:`t_k` given by the
            integration weights method
        :return: a single complex value or a complex array of the shape of t that holds the values of
            stochastic process at times t
        """
        if self._z is None:
            raise RuntimeError("StocProc has NO random data, call 'new_process' to generate a new random process")

        if t is None:
            return self._z
        else:
            return self._interpolator(t)

    @abc.abstractmethod
    def calc_z(self, y):
        r"""
        maps the normal distributed complex valued random variables y to the stochastic process
        
        :return: the stochastic process, array of complex numbers 
        """
        pass
    
    def _calc_scaled_z(self, y):
        r"""scaled the discrete process z with sqrt(scale), such that <z_i z^ast_j> = scale bcf(i,j)"""
        return self.sqrt_scale * self.calc_z(y)

    @abc.abstractmethod
    def get_num_y(self):
        r"""
        :return: number of complex random variables needed to calculate the stochastic process 
        """
        pass        
    
    def get_time(self):
        r"""Returns the time :math:`t_k` corresponding to the values :math:`z_k`

        These times are determined by the integration weights method.
        """
        return self.t
    
    def get_z(self):
        r"""Returns the discrete process :math:`z_k`."""
        return self._z
    
    def new_process(self, y=None, seed=None):
        r"""generate a new process by evaluating :py:func:`calc_z` with new random variables :math:`Y_i`

        :param y: independent normal distributed complex valued random variables with :math:`\sigma_{ij}^2 = \langle y_i y_j^\ast \rangle = 2 \delta_{ij}`
        :param seed: if not None set seed to seed before generating samples
        
        When y is given use these random numbers as input for :py:func:`calc_z`
        otherwise generate a new set of random numbers.
        """
        t0 = time.time()
        self._interpolator = None
        self._proc_cnt += 1
        if seed != None:
            log.info("use fixed seed ({})for new process".format(seed))
            np.random.seed(seed)
        if y is None:
            #random complex normal samples
            y = np.random.normal(scale=self._one_over_sqrt_2, size = 2*self.get_num_y()).view(np.complex)
        del self._z
        self._z = self._calc_scaled_z(y)
        log.debug("proc_cnt:{} new process generated [{:.2e}s]".format(self._proc_cnt, time.time() - t0))
        t0 = time.time()
        self._interpolator = fcSpline.FCS(x_low=0, x_high=self.t_max, y=self._z)
        log.debug("created interpolator [{:.2e}s]".format(time.time() - t0))
        
    def set_scale(self, scale):
        self.scale = scale
        self.sqrt_scale = np.sqrt(scale)


class StocProc_KLE(_absStocProc):
    r"""
        A class to simulate stochastic processes using Karhunen-Loève expansion (KLE) method.
        The idea is that any stochastic process can be expressed in terms of the KLE

        .. math:: Z(t) = \sum_i \sqrt{\lambda_i} Y_i u_i(t)

        where :math:`Y_i` and independent complex valued Gaussian random variables with variance one
        (:math:`\langle Y_i Y_j \rangle = \delta_{ij}`) and :math:`\lambda_i`, :math:`u_i(t)` are
        eigenvalues / eigenfunctions of the following homogeneous Fredholm equation

        .. math:: \int_0^{t_\mathrm{max}} \mathrm{d}s R(t-s) u_i(s) = \lambda_i u_i(t)

        for a given positive integral kernel :math:`R(\tau)`. It turns out that the auto correlation of the
        stocastic processes :math:`\langle Z(t)Z^\ast(s) \rangle = R(t-s)` is given by that kernel.

        For the numeric implementation the integral equation will be discretized
        (see :py:func:`stocproc.method_kle.solve_hom_fredholm` for details) which leads to a regular matrix
        eigenvalue problem.
        The accuracy of the generated  process in terms of its auto correlation function depends on
        the quality of the eigenvalues and eigenfunction and thus of the number of discritization points.
        Further for a given threshold there is only a finite number of eigenvalues above that threshold,
        provided that the number of discritization points is large enough.

        Now the property of representing the integral kernel in terms of the eigenfunction

        .. math :: R(t-s) = \sum_i \lambda_i u_i(t) u_i^\ast(s)

        is used to find the number of discritization points and the number of used eigenfunctions such that
        the sum represents the kernel up to a given tolerance (see :py:func:`stocproc.method_kle.auto_ng`
        for details).
    """
    
    def __init__(self, r_tau, t_max, tol=1e-2, ng_fac=4, meth='fourpoint', diff_method='full', dm_random_samples=10**4,
        seed=None, align_eig_vec=False, scale=1):
        """
        :param r_tau: the idesired auto correlation function of a single parameter tau
        :param t_max: specifies the time interval [0, t_max] for which the processes in generated
        :param tol: maximal deviation of the auto correlation function of the sampled processes from
            the given auto correlation r_tau.
        :param ngfac: specifies the fine grid to use for the spline interpolation, the intermediate points are
            calculated using integral interpolation
        :param meth: the method for calculation integration weights and times, a callable or one of the following strings
            'midpoint' ('midp'), 'trapezoidal' ('trapz'), 'simpson' ('simp'), 'fourpoint' ('fp'),
            'gauss_legendre' ('gl'), 'tanh_sinh' ('ts')
        :param diff_method: either 'full' or 'random', determines the points where the above success criterion is evaluated,
            'full': full grid in between the fine grid, such that the spline interpolation error is expected to be maximal
            'random': pick a fixed number of random times t and s within the interval [0, t_max]
        :param dm_random_samples: the number of random times used for diff_method 'random'
        :param seed: if not None seed the random number generator on init of this class with seed
        :param align_eig_vec: assures that :math:`re(u_i(0)) \leq 0` and :math:`im(u_i(0)) = 0` for all i

        .. note ::
           To circumvent the time consuming initializing the StocProc class can be saved and loaded using
           the standard python pickle module. The :py:func:`get_key` method may be used identify the
           Process class by its parameters (r_tau, t_max, tol).

        .. seealso ::
           Details on how to solve the homogeneous Fredholm equation: :py:func:`stocproc.method_kle.solve_hom_fredholm`

           Details on the error estimation and further clarification of the parameters ng_fac, meth,
           diff_method, dm_random_samples can be found at :py:func:`stocproc.method_kle.auto_ng`.
        """
        key = r_tau, t_max, tol
        
        sqrt_lambda_ui_fine, t = method_kle.auto_ng(corr=r_tau,
                                                    t_max=t_max,
                                                    ngfac=ng_fac,
                                                    meth=meth,
                                                    tol=tol,
                                                    diff_method=diff_method,
                                                    dm_random_samples=dm_random_samples)

        # inplace alignment such that re(ui(0)) >= 0 and im(ui(0)) = 0
        if align_eig_vec:
            method_kle.align_eig_vec(sqrt_lambda_ui_fine)

        state = sqrt_lambda_ui_fine, t, seed, scale, key
        self.__setstate__(state)

    @staticmethod
    def get_key(r_tau, t_max, tol=1e-2):
        return r_tau, t_max, tol
        

    # def get_key(self):
    #     """Returns the tuple (r_tau, t_max, tol) which should suffice to identify the process in order to load/dump
    #     the StocProc class.
    #     """
    #     return self.key

    def __bfkey__(self):
        return self.key

    def __getstate__(self):
        return self.sqrt_lambda_ui_fine, self.t, self._seed, self.scale, self.key

    def __setstate__(self, state):
        sqrt_lambda_ui_fine, t, seed, scale, self.key = state
        num_ev, ng = sqrt_lambda_ui_fine.shape
        super().__init__(t_axis=t, seed=seed, scale=scale)
        self.num_ev = num_ev
        self.sqrt_lambda_ui_fine = sqrt_lambda_ui_fine

    def calc_z(self, y):
        r"""evaluate :math:`z_k = \sum_i \lambda_i Y_i u_{ik}`"""
        return np.tensordot(y, self.sqrt_lambda_ui_fine, axes=([0], [0])).flatten()

    def get_num_y(self):
        """The number of independent random variables Y is given by the number of used eigenfunction
        to approximate the auto correlation kernel.
        """
        return self.num_ev


class StocProc_FFT(_absStocProc):
    r"""Simulate Stochastic Process using FFT method

    This method uses the relation of the auto correlation to the non negative real valued
    spectral density :math:`J(\omega)`. The integral can be approximated by a discrete integration scheme

    .. math::
        \alpha(\tau) = \int_{\omega_\mathrm{min}}^{\omega_\mathrm{max}} \mathrm{d}\omega \, \frac{J(\omega)}{\pi} e^{-\mathrm{i}\omega \tau}
        \approx \sum_{k=0}^{N-1} w_k \frac{J(\omega_k)}{\pi} e^{-\mathrm{i} \omega_k \tau}

    where the weights :math:`\omega_k` depend on the particular integration scheme. For a process defined as

    .. math:: Z(t) = \sum_{k=0}^{N-1} \sqrt{\frac{w_k J(\omega_k)}{\pi}} Y_k \exp^{-\mathrm{i}\omega_k t}

    with independent complex random variables :math:`Y_k` such that :math:`\langle Y_k \rangle = 0`,
    :math:`\langle Y_k Y_{k'}\rangle = 0` and :math:`\langle Y_k Y^\ast_{k'}\rangle = \delta_{k,k'}`
    it is easy to see that its auto correlation function will be exactly the approximated auto correlation function.

    .. math::
        \begin{align}
            \langle Z(t) Z^\ast(s) \rangle = & \sum_{k,k'} \frac{1}{\pi} \sqrt{w_k w_{k'} J(\omega_k)J(\omega_{k'})} \langle Y_k Y_{k'}\rangle \exp(-\mathrm{i}(\omega_k t - \omega_k' s)) \\
                                           = & \sum_{k}    \frac{w_k}{\pi} J(\omega_k) e^{-\mathrm{i}\omega_k (t-s)}
                                           \approx & \alpha(t-s)
        \end{align}

    To calculate :math:`Z(t)` the Discrete Fourier Transform (DFT) can be applied as follows:

    .. math:: Z(t_l) = e^{-\mathrm{i}\omega_\mathrm{min} t_l} \sum_{k=0}^{N-1} \sqrt{\frac{w_k J(\omega_k)}{\pi}} Y_k  e^{-\mathrm{i} 2 \pi \frac{k l}{N} \frac{\Delta \omega \Delta t}{ 2 \pi} N}

    Here :math:`\omega_k` has to take the form :math:`\omega_k = \omega_\mathrm{min} + k \Delta \omega` and
    :math:`\Delta \omega = (\omega_\mathrm{max} - \omega_\mathrm{min}) / (N-1)` which limits
    the itegration schemes to those with equidistant weights.
    For the DFT scheme to be applicable :math:`\Delta t` has to be chosen such that
    :math:`2\pi = N \Delta \omega \Delta t` holds.
    Since :math:`J(\omega)` is real it follows that :math:`X(t_l) = X^\ast(t_{N-l})`.
    For that reason the stochastic process has only :math:`(N+1)/2` (odd :math:`N`) and
    :math:`(N/2 + 1)` (even :math:`N`) independent time grid points.

    To generate a process with given auto correlation function on the interval [0, t_max]
    requires that the auto correlation function approximation is valid for all t in [0, t_max].

    This is ensured by automatically determining the number of sumands N and the integral
    boundaries :math:`\omega_\mathrm{min}` and :math:`\omega_\mathrm{max}` such that
    discrete Fourier transform of the spectral density matches the desired auto correlation function
    within the tolerance intgr_tol for all discrete :math:`t_l \in [0, t_\mathrm{max}]`.

    As the time continuous process is generated via cubic spline interpolation, the deviation
    due to the interpolation is controlled by the parameter intpl_tol. The maximum time step :math:`\Delta t`
    is chosen such that the interpolated valued at each half step :math:`t_i + \Delta t /2` differs at
    most intpl_tol from the exact value of the auto correlation function.

    If not fulfilled already N and the integration boundaries are increased such that the :math:`\Delta t`
    criterion from the interpolation is met.


    :param spectral_density: the spectral density :math:`J(\omega)` as callable function object
    :param t_max: :math:`[0,t_\mathrm{max}]` is the interval for which the process will be calculated
    :param bcf_ref: a callable which evaluates the Fourier integral exactly
    :param intgr_tol: tolerance for the integral approximation
    :param intpl_tol: tolerance for the interpolation
    :param seed: if not None, use this seed to seed the random number generator
    :param negative_frequencies: if False, keep :math:`\omega_\mathrm{min} = 0` otherwise
       find a negative :math:`\omega_\mathrm{min}` appropriately just like :math:`\omega_\mathrm{max}

    .. todo::
       implement bcf_ref = None and use numeric integration as default


    """
    def __init__(self, spectral_density, t_max, bcf_ref, intgr_tol=1e-2, intpl_tol=1e-2,
                 seed=None, negative_frequencies=False, scale=1):
        self.key = bcf_ref, t_max, intgr_tol, intpl_tol
                
        if not negative_frequencies: 
            log.info("non neg freq only")
            a, b, N, dx, dt = method_fft.calc_ab_N_dx_dt(integrand = spectral_density,
                                                         intgr_tol = intgr_tol,
                                                         intpl_tol = intpl_tol,
                                                         t_max     = t_max,
                                                         ft_ref    = lambda tau:bcf_ref(tau)*np.pi,
                                                         opt_b_only= True)
        else:
            log.info("use neg freq")
            a, b, N, dx, dt = method_fft.calc_ab_N_dx_dt(integrand = spectral_density,
                                                         intgr_tol = intgr_tol,
                                                         intpl_tol = intpl_tol,
                                                         t_max     = t_max,
                                                         ft_ref    = lambda tau:bcf_ref(tau)*np.pi,
                                                         opt_b_only= False)

        assert abs(2*np.pi - N*dx*dt) < 1e-12

        print("Fourier Integral Boundaries: [{:.3e}, {:.3e}]".format(a,b))
        print("Number of Nodes            : {}".format(N))
        print("yields dx                  : {:.3e}".format(dx))
        print("yields dt                  : {:.3e}".format(dt))
        print("yields t_max               : {:.3e}".format( (N-1)*dt))

        num_grid_points = int(np.ceil(t_max/dt))+1

        assert num_grid_points <= N

        t_max = (num_grid_points-1)*dt
        
        super().__init__(t_max           = t_max, 
                         num_grid_points = num_grid_points, 
                         seed            = seed,
                         scale           = scale)
        
        self.yl = spectral_density(dx*np.arange(N) + a + dx/2) * dx / np.pi
        self.yl = np.sqrt(self.yl)
        self.omega_min_correction = np.exp(-1j*(a+dx/2)*self.t)   #self.t is from the parent class

    @staticmethod
    def get_key(t_max, bcf_ref, intgr_tol=1e-2, intpl_tol=1e-2):
        return bcf_ref, t_max, intgr_tol, intpl_tol

    def __getstate__(self):
        return self.yl, self.num_grid_points, self.omega_min_correction, self.t_max, self._seed, self.scale, self.key

    def __setstate__(self, state):
        self.yl, num_grid_points, self.omega_min_correction, t_max, seed, scale, self.key = state
        super().__init__(t_max           = t_max,
                         num_grid_points = num_grid_points,
                         seed            = seed,
                         scale           = scale)
            
    def calc_z(self, y):
        r"""calculate

        .. math::
            Z(t_l) = e^{-\mathrm{i}\omega_\mathrm{min} t_l} \mathrm{DFT}\left( \sqrt{\frac{w_k J(\omega_k)}{\pi}} Y_k \right)

        and return values with :math:`t_l < t_\mathrm{max}`
        """
        z_fft = np.fft.fft(self.yl * y)
        z = z_fft[0:self.num_grid_points] * self.omega_min_correction
        return z

    def get_num_y(self):
        r"""The number of independent random variables Y is given by the number of discrete times
        :math:`t_l < t_\mathrm{max}` from the Fourier Transform
        """
        return len(self.yl)


class StocProc_TanhSinh(_absStocProc):
    r"""Simulate Stochastic Process using TanhSinh integration for the Fourier Integral  
    """

    def __init__(self, spectral_density, t_max, bcf_ref, intgr_tol=1e-2, intpl_tol=1e-2,
                 seed=None, negative_frequencies=False, scale=1):
        self.key = bcf_ref, t_max, intgr_tol, intpl_tol

        if not negative_frequencies:
            log.info("non neg freq only")
            log.info("get_dt_for_accurate_interpolation, please wait ...")
            try:
                ft_ref = lambda tau: bcf_ref(tau) * np.pi
                c = method_fft.find_integral_boundary(lambda tau: np.abs(ft_ref(tau)) / np.abs(ft_ref(0)),
                                                      intgr_tol, 1, 1e6, 0.777)
            except RuntimeError:
                c = t_max

            c = min(c, t_max)
            dt_tol = method_fft.get_dt_for_accurate_interpolation(t_max=c,
                                                                  tol=intpl_tol,
                                                                  ft_ref=ft_ref)
            log.info("requires dt < {:.3e}".format(dt_tol))
        else:
            raise NotImplementedError

        N = int(np.ceil(t_max/dt_tol))
        log.info("yields N = {}".format(N))

        wmax = method_fft.find_integral_boundary(spectral_density, tol=intgr_tol/4, ref_val=1, max_val=1e6, x0=0.777)

        h = 0.1
        k = 15
        kstep = 5
        conv_fac = 1
        old_d = None
        log.info("find h and kmax for TanhSinh integration ...")
        while True:
            tau = np.asarray([t_max])
            num_FT, fb = method_fft.fourier_integral_TanhSinh_with_feedback(integrand=lambda w: spectral_density(w) / np.pi,
                                                                            w_max=wmax,
                                                                            tau=tau,
                                                                            h=h,
                                                                            kmax=k)
            d = np.abs(num_FT - bcf_ref(tau)) / np.abs(bcf_ref(0))
            print("fb", fb, "d", d)
            if fb == 'ok':
                k += kstep
            else:
                log.info("lowest diff with h {:.3e}: {:.3e} < tol ({:.3e}) -> new h {:.3e}".format(h, d[0], intgr_tol, h/2))
                h /= 2
                k = 15
                kstep *= 2

                if old_d is None:
                    old_d = d[0]
                else:
                    if old_d < conv_fac * d[0]:
                        wmax *= 1.5
                        log.info("convergence factor of {} not met -> inc wmax to {}".format(conv_fac, wmax))
                        h = 0.1
                        k = 15
                        kstep = 5
                        old_d = None
                    else:
                        old_d = d[0]

            if d < intgr_tol:
                log.info("intgration tolerance met with h {} and kmax {}".format(h, k))
                break

        tau = np.linspace(0, (N-1)*dt_tol, N)
        num_FT = method_fft.fourier_integral_TanhSinh(
            integrand=lambda w: spectral_density(w) / np.pi,
            w_max=wmax,
            tau=tau,
            h=h,
            kmax=k)
        d = np.max(np.abs(num_FT - bcf_ref(tau)) / np.abs(bcf_ref(0)))
        assert d < intgr_tol, "d:{}, intgr_tol:{}".format(d, intgr_tol)

        wk = [method_fft.wk(h, ki) for ki in range(1, k+1)]
        wk = np.hstack((wk[::-1], [method_fft.wk(h, 0)], wk))

        yk = np.asarray([method_fft.yk(h, ki) for ki in range(1, k+1)])
        tmp1 = wmax/2
        self.omega_k = np.hstack( (yk[::-1] * tmp1, tmp1, (2 - yk) * tmp1))
        self.fl = np.sqrt(tmp1*wk*spectral_density(self.omega_k)/np.pi)

        super().__init__(t_max=t_max,
                         num_grid_points=N,
                         seed=seed,
                         scale=scale)


    @staticmethod
    def get_key(t_max, bcf_ref, intgr_tol=1e-2, intpl_tol=1e-2):
        return bcf_ref, t_max, intgr_tol, intpl_tol

    def __getstate__(self):
        return self.fl, self.omega_k, self.num_grid_points, self.t_max, self._seed, self.scale, self.key

    def __setstate__(self, state):
        self.fl, self.omega_k, num_grid_points, t_max, seed, scale, self.key = state
        super().__init__(t_max=t_max,
                         num_grid_points=num_grid_points,
                         seed=seed,
                         scale=scale)

    def calc_z(self, y):
        r"""calculate
    
        .. math::
            Z(t_l) = sum_k \sqrt{\frac{w_k J(\omega_k)}{\pi}} Y_k e^{-\i \omega_k t_l}
        """
        z = np.empty(shape=self.num_grid_points, dtype=np.complex128)
        for i, ti in enumerate(self.t):
            z[i] = np.sum(self.fl*y*np.exp(-1j*self.omega_k*ti))

        return z

    def get_num_y(self):
        return len(self.fl)