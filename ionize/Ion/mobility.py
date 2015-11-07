from math import copysign, sqrt
import warnings
import numpy as np

from ..constants import pitts, reference_temperature, kelvin, faraday, \
    elementary_charge, lpm3, gpkg, onsager_fuoss


def mobility(self, pH=None, ionic_strength=None, temperature=None):
    """Return the effective mobility of the ion at a given pH and I.

    Args:
        pH (float): The ambiant pH.

        I (float): The ambiant ionic strength.

    If an actual mobility from Onsager-Fouss is available, it is used,
    otherwise, the Robinson-Stokes mobility estimate is used.

    If the Ion is nested in a Solution, ok to call without a pH.

    >>> Solution(myIon, .1).ions[0].effective_mobility()

    Otherwise, always call with a pH argument.
    """
    pH, ionic_strength, temperature = \
        self._resolve_context(pH, ionic_strength, temperature)

    ionization_fraction = self.ionization_fraction(pH, ionic_strength,
                                                   temperature)
    actual_mobility = self.actual_mobility(ionic_strength, temperature)

    effective_mobility = np.sum(ionization_fraction * actual_mobility)

    return effective_mobility


def actual_mobility(self, ionic_strength=None, temperature=None):
    try:
        return self.onsager_fuoss_mobility()
    except AttributeError:
        warnings.warn("Insufficient information for Onsager-Fuoss "
                      "correction to mobility. Returning the Robinson-"
                      "Stokes approximation.")
    return self.robinson_stokes_mobility(ionic_strength, temperature)


def absolute_mobility(self, temperature=None):
    _, _, temperature = \
        self._resolve_context(None, None, temperature)

    if self._nightingale_function:
        absolute_mobility = \
            (self._nightingale_function(temperature).tolist() *
             10.35e-11 / self._solvent.viscosity(temperature) *
             self.valence)

        if not (self.nightingale_data['min'] <
                temperature <
                self.nightingale_data['max']):
            warnings.warn('Temperature outside range'
                          'for nightingale data.')
    else:
        absolute_mobility =\
            (self._solvent.viscosity(self.reference_temperature) /
             self._solvent.viscosity(temperature)*self.reference_mobility)

    return absolute_mobility


def robinson_stokes_mobility(self, ionic_strength=None, temperature=None):
    """Return the Robinson-Stokes correction to fully ionized mobility.

    This correction is appropriate if a generic ionic strength is known,
    but the specific ions in solution are unknown.
    """
    # Currently using the ionic strength where Bahga 2010
    # uses twice the ionic strength. This appears to work, and follows the
    # SPRESSO implimentation.
    # Likely typo in paper.
    # TODO: Recheck this typo.
    _, ionic_strength, temperature = \
        self._resolve_context(None, ionic_strength, temperature)

    dielectric = self._solvent.dielectric(temperature)
    viscosity = self._solvent.viscosity(temperature)

    # TODO: Check again.
    alpha = 1.705 * (kelvin(temperature) / dielectric) ** (-3./2.)
    beta = 4.275e-9 / sqrt(kelvin(temperature) * dielectric) /\
        viscosity

    mobility = self.absolute_mobility(temperature)
    mobility -= (alpha * mobility +
                 beta * np.sign(self.valence)
                 ) * (sqrt(ionic_strength) /
                      (1. + pitts * sqrt(ionic_strength))
                      )
    return mobility


def onsager_fuoss_mobility(self):
    _, ionic_strength, temperature = \
        self._resolve_context(None, None, None)

    dielectric = self._solvent.dielectric(temperature)
    viscosity = self._solvent.viscosity(temperature)
    interaction = _interaction(self.context(), self)

    # New temperature corrected coefficients.
    alpha = 1.971e6 * sqrt(2) /\
        (kelvin(temperature) * dielectric)**(3./2.)

    # TODO: Check this scaling. Based on magnitude, unsure on units.
    beta = (28.98 * sqrt(2) * faraday * elementary_charge * lpm3 /
            sqrt(kelvin(temperature) * dielectric) / viscosity)

    mobility = self.absolute_mobility()
    mobility -= (alpha * mobility * interaction * np.abs(self.valence) +
                 beta * np.sign(self.valence)) * \
        (sqrt(ionic_strength) / (1. + pitts * sqrt(ionic_strength)))

    return mobility


def _interaction(self, ion):
    """Return the Onsager-Fuoss correction to the mobilities of ions.

    This function returns a list of all corrected actual mobilities.
    These mobilities are automatically assigned to the correct ions when
    a solution is initialized."""

    ions = [ion_ for ion_ in self.ions if self.concentration(ion_) > 0] + \
           [self._hydroxide,  self._hydronium]

    if ion not in ions:
        ions = ions + [ion]
    ion_index = ions.index(ion)
    if ion_index != 0:
        start_index = len(np.concatenate([ion_.valence
                                          for ion_ in ions[:ion_index]]))
    else:
        start_index = 0
    end_index = start_index + len(ion.valence)

    omega = np.concatenate([ion.absolute_mobility() / ion.valence
                            for ion in ions]) / faraday

    if np.any(omega == 0.):
        raise RuntimeError('Onsager-Fuoss approximation '
                           'diverges for non-mobile ions. ')

    valences = np.concatenate([ion.valence for ion in ions])
    concentrations = np.concatenate([self.concentration(ion) *
                                     ion.ionization_fraction(self.pH)
                                     for ion in ions])

    potential = concentrations * valences**2. / (2. * self.ionic_strength)

    h = potential * omega / (omega + omega[:, np.newaxis])
    d = np.diag(np.sum(h, 1))
    B = 2 * (h + d) - np.identity(len(omega))

    r = np.zeros([len(omega), 6])
    r[:, 0] = (valences - (np.sum(valences*potential) /
                           np.sum(potential/omega)) / omega)

    for i in range(1, 6):
        r[:, i] = np.dot(B, r[:, i-1])

    factor = np.dot(onsager_fuoss, np.transpose(r))

    return factor[start_index:end_index]