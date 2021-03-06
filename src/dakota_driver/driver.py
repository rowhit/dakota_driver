"""
A collection of drivers using DAKOTA to exercise the workflow.
The general scheme is to have a separate class for each separate DAKOTA
method type.

Currently these drivers simply run the workflow, they do not parse any
DAKOTA results.
"""
#from openmdao.util.record_util import create_local_meta
from numpy import array
import re
from mpi4py.MPI import COMM_WORLD as world
import collections

from dakota import DakotaInput, run_dakota
from six import iteritems, itervalues

#from openmdao.drivers.predeterminedruns_driver import PredeterminedRunsDriver
#from openmdao.core.driver import Driver 
#from openmdao.util.record_util import create_local_meta, update_local_meta
import sys
from openmdao.main.hasparameters import HasParameters
from openmdao.main.hasconstraints import HasIneqConstraints
from openmdao.main.hasobjective import HasObjectives
from openmdao.main.interfaces import IHasParameters, IHasIneqConstraints, \
                                     IHasObjectives, IOptimizer, implements
from openmdao.main.driver import Driver
from openmdao.util.decorators import add_delegate
import numpy as np
__all__ = ['DakotaCONMIN', 'DakotaMultidimStudy', 'DakotaVectorStudy',
           'DakotaGlobalSAStudy', 'DakotaOptimizer', 'DakotaBase']

_SET_AT_RUNTIME = "SPECIFICATION DECLARED BUT NOT DEFINED"


@add_delegate(HasParameters, HasObjectives)
#class DakotaBase(PredeterminedRunsDriver):
class DakotaBase(Driver):
    """
    Base class for common DAKOTA operations, adds :class:`DakotaInput` instance.
    The ``method`` and ``responses`` sections of `input` must be set
    directly.  :meth:`set_variables` is typically used to set the ``variables``
    section.
    """

    implements(IHasParameters, IHasObjectives)

    output = 'normal',
    #output = Enum('normal', iotype='in', desc='Output verbosity',
    #              values=('silent', 'quiet', 'normal', 'verbose', 'debug'))
    stdout = ''
    stderr = ''
    tabular_graphics_data = True
            

    def __init__(self):
        super(DakotaBase, self).__init__()

        # allow for special variable distributions
        self.special_distribution_variables = []
        self.clear_special_variables()
 
        self.configured = None
        # Set baseline input, don't touch 'interface'.
        self.input = DakotaInput(environment=[],
                                 method=[],
                                 model=['single'],
                                 variables=[],
                                 responses=[])

    def check_config(self, strict=False):
        """ Verify valid configuration. """
        super(DakotaBase, self).check_config(strict=strict)

        parameters = self.get_parameters()
        if not parameters and not self.special_distribution_variables:
            self.raise_exception('No parameters, run aborted', ValueError)

        objectives = self.get_objectives()
        if not objectives:
            self.raise_exception('No objectives, run aborted', ValueError)

    def run_dakota(self):
        """
        Call DAKOTA, providing self as data, after enabling or disabling
        tabular graphics data in the ``environment`` section.
        DAKOTA will then call our :meth:`dakota_callback` during the run.
        """
        parameters = self.get_parameters()
        #parameters = self._desvars
        if not parameters:
            self.raise_exception('No parameters, run aborted', ValueError)

        if not self.methods:
            raise ValueError('Method not set')
        if not self.input.variables:
            self.raise_exception('Variables not set', ValueError)
        if not self.input.responses:
            self.raise_exception('Responses not set', ValueError)

        for i, line in enumerate(self.input.environment):
            if 'tabular_graphics_data' in line:
                if not self.tabular_graphics_data:
                    self.input.environment[i] = \
                        line.replace('tabular_graphics_data', '')
                break
        else:
            if self.tabular_graphics_data:
                self.input.environment.append('tabular_graphics_data')

        infile = self.name+ '.in'
        self.input.write_input(infile, data=self)
        #self.input.write_input(infile, data=self, other_data=self.other_model)
        #from openmdao.core.mpi_wrap import MPI
        from mpi4py import MPI
        run_dakota(infile, use_mpi=True, mpi_comm = self.mpi_comm, stdout=self.stdout, stderr=self.stderr, restart=0)
        #if MPI:
        #    if self.mpi_comm:
        #       run_dakota(infile, use_mpi=True, mpi_comm = self.mpi_comm, stdout=self.stdout, stderr=self.stderr, restart=self.dakota_hotstart)
        #    else:
        #       run_dakota(infile, use_mpi=True, stdout=self.stdout, stderr=self.stderr, restart=self.dakota_hotstart)
        #try:
        #    run_dakota(infile, stdout=self.stdout, stderr=self.stderr)
        #except Exception:
        #    print sys.exc_info()
        #    exc_type, exc_value, exc_traceback = sys.exc_info()
        #    raise type('%s' % exc_type), exc_value, exc_traceback

           # self.reraise_exception()

    def dakota_callback(self, **kwargs):
        """
        Return responses from parameters.  `kwargs` contains:

        ========== ==============================================
        Key        Definition
        ========== ==============================================
        functions  number of functions (responses, constraints)
        ---------- ----------------------------------------------
        variables  total number of variables
        ---------- ----------------------------------------------
	cv         list/array of continuous variable values
        ---------- ----------------------------------------------
        div        list/array of discrete integer variable values
        ---------- ----------------------------------------------
        drv        list/array of discrete real variable values
        ---------- ----------------------------------------------
        av         single list/array of all variable values
        ---------- ----------------------------------------------
        cv_labels  continuous variable labels
        ---------- ----------------------------------------------
        div_labels discrete integer variable labels
        ---------- ----------------------------------------------
        drv_labels discrete real variable labels
        ---------- ----------------------------------------------
        av_labels  all variable labels
        ---------- ----------------------------------------------
        asv        active set vector (bit1=f, bit2=df, bit3=d^2f)
        ---------- ----------------------------------------------
        dvv        derivative variables vector
        ---------- ----------------------------------------------
        currEvalId current evaluation ID number
        ========== ==============================================

        """
        cv = kwargs['cv']
        asv = kwargs['asv']
        dvv = kwargs['dvv']
        av_labels = kwargs['av_labels']


        self.set_parameters(cv)
        self.run_iteration()

        expressions = self.get_objectives().values()
        if hasattr(self, 'get_eq_constraints'):
            expressions.extend(self.get_eq_constraints().values())
        if hasattr(self, 'get_ineq_constraints'):
            expressions.extend(self.get_ineq_constraints().values())

        fns = []
        fnGrads = []
        for i, expr in enumerate(expressions):
            if asv[i] & 1:
                val = expr.evaluate(self.parent)
                if isinstance(val, list):
                    fns.extend(val)
                else:
                    fns.append(val)
            if asv[i] & 2:
               val = expr.evaluate_gradient(self.parent)
               fnGrads.append(val)
               # self.raise_exception('Gradients not supported yet',
               #                      NotImplementedError)
            if asv[i] & 4:
                self.raise_exception('Hessians not supported yet',
                                     NotImplementedError)

        retval = dict(fns=array(fns), fnGrads = array(fnGrads))
        self._logger.debug('returning %s', retval)
        return retval


        #print 'av_labs are ',av_labels , ' and cv is ', cv; quit()
        #self._logger.debug('cv %s', cv)
        #self._logger.debug('asv %s', asv)

        #dvlist = [s for s in self.special_distribution_variables if s not in self.array_desvars]
        #if True: #self.array_desvars:
        #    for i, var  in enumerate(av_labels):
        #        if var in self.root.unknowns._dat.keys(): self.set_desvar(var, cv[i])
        #        elif re.findall("(.*)\[(.*)\]", var)[0][0] in self.root.unknowns._dat.keys(): 
        #            #print 'setting ',re.findall("(.*)\[(.*)\]", var)[0][0], int(re.findall("(.*)\[(.*)\]", var)[0][1]), ' as ', cv[i]
        #            self.set_desvar(re.findall("(.*)\[(.*)\]", var)[0][0], cv[i], index=[int(re.findall("(.*)\[(.*)\]", var)[0][1])])
        #else:
        #    dvl = dvlist + self._desvars.keys() +  self.special_distribution_variables
        #    #dvl = dvlist + self._desvars.keys() 
        #    for i  in range(len(cv)):
        #        if dvl[i] in self.root.unknowns._dat.keys(): self.set_desvar(dvl[i], cv[i])
        #        #self.set_desvar(dvl[i], cv[i])
        #        elif re.findall("(.*)\[(.*)\]", dvl[i])[0][0] in self.root.unknowns._dat.keys(): 
        #            self.set_desvar(re.findall("(.*)\[(.*)\]", dvl[i])[0][0], cv[i], index=[int(re.findall("(.*)\[(.*)\]", dvl[i])[0][1])])
        #system = self.root
        #metadata = self.metadata  = create_local_meta(None, 'pydakrun%d'%world.Get_rank())
        #system.ln_solver.local_meta = metadata
        #self.iter_count += 1
        #update_local_meta(metadata, (self.iter_count,))
        #self.root.solve_nonlinear()

            #system.solve_nonlinear(metadata=metadata)
        #self.recorders.record_iteration(system, metadata)

        #expressions = self.get_objectives().values()[0].tolist()#.update(self.get_constraints())
        #cons = self.get_constraints()
        #for c in cons:
        #       #expressions.append(-1*c)
        #       expressions.append(-1*self.get_constraints()[con])

        #expressions = []
        #for key in self.get_objectives():
        #   expressions += list(self.get_objectives()[key])
        #for con in self.get_constraints().values():
        #    for c in con:
        #       expressions.append(-1*c)


        #fns = []
        #fnGrads = []

        #for i in range(len(asv)):
        #    val = expressions[i]

        #    if asv[i] & 1 or asv[i]==0:
        #       fns.extend([val])
        #    if asv[i] & 2:
        #       objs = self.get_objectives().keys()
        #       gvars = []
        #       gvars_list = [] # we need to strip the descriptors of the [n] index
        #       gindexes = {} # then keep only the indexes of interest for each desvar
        #       seen = set()
        #       print kwargs['av_labels']  
        #       vars_for_grads = kwargs['av_labels']
        #       for var in vars_for_grads:
        #           if var in self.root.unknowns._dat.keys(): gvars.append(var)
        #           else:
        #               vname = re.findall("(.*)\[(.*)\]", var)[0][0]
        #               if vname not in self.root.unknowns._dat.keys(): 
        #                   raise ValueError("%s not in desvars"%vname)
        #               if vname not in seen:
        #                   seen.add(vname)
        #                   gvars_list.append(vname)
        #               ind = int(re.findall("(.*)\[(.*)\]", var)[0][1])
        #               if vname not in gindexes:
        #                   gindexes[vname] = [ind]
        #               else: gindexes[vname].append(ind)
#
#               # only supporting one objective for now. I'll have to find out more about 
#               # the ASV structure before continuing.
#               for gvar in gvars_list:
#                   print gvars_list
#                   print 'gvar ', gvar
#                   grad = self._prob.calc_gradient([gvar], self.get_objectives().keys())[0]
#                   for ind in gindexes[gvar]:
#                       print '  index ', ind
#                       fnGrads.append(grad[ind])
#               for gvar in gvars:
#                   grad = self._prob.calc_gradient([gvar], self.get_objectives().keys())[0]
#                   fnGrads.extend(grad)
#               fnGrads = np.array([fnGrads])
#                   #print 'hey. grad is ', grad ; quit()
#               #for lab in kwargs['av_labels']:
#                  #fnGrads.extend([val])
#            #fnGrads.append([val])
#            # self.raise_exception('Gradients not supported yet',
#            #                      NotImplementedError)
#            if asv[i] & 4:
#               self.raise_exception('Hessians not supported yet',
#                                     NotImplementedError)
#
#        retval = dict(fns=array(fns), fnGrads = array(fnGrads))
#       # print 'asv was ',asv
#       # print 'returning ',retval
#        #self._logger.debug('returning %s', retval)
#        return retval
#
    # We fully configure the input just before running the analysis as the user is liable to set
    # several aspects of the optimization problem after calling pydakdriver.
    # We only set the variables and responses blocks here, as the other input blocks are not dependant on
    # additional configurations to the analysis.
    def configure_input(self):
        """ Configures input specification, must be overridden. """


        # CONFIGURE VARIABLES

        # Find regular parameters
        parameters = []  # [ [name, value], ..]
        dvars = self.get_parameters()
        dvar_values = self.eval_parameters(dtype=None)
        self.reg_params = parameters
        for n, param in enumerate(dvars):
           # if len(dvars[param]) == 1:
                parameters.append([param, dvar_values[0]])
           # else:
           #     for i, val in enumerate(dvars[param]):
           #         parameters.append([param + '[' + str(i) + ']', val])
           #         self.array_desvars.append(param + '[' + str(i) + ']')

        self.input.reg_variables.append('continuous_design = %s' % len(parameters))

        secondaryV = False
        
        for i in range(len(self.input.model)):
           if 'secondary_variable_mapping' in self.input.model[i]: secondaryV=True
        if parameters: 
            state_params = []
            for param in parameters:
                if param not in self.special_distribution_variables: state_params.append(param)
            if state_params: self.input.state_variables.append('continuous_state = %s' % len(state_params))

        initial = []  # initial points of regular paramters
        for val in  self.eval_parameters():
        #for val in self.get_desvars().values():
        #    if isinstance(val, collections.Iterable):
        #        initial.extend(val)
        #    else:
                initial.append(val)
        self.input.reg_variables.append(
            '  initial_point %s' % ' '.join(str(s) for s in initial))
        if initial: self.input.state_variables.append(
            '  initial_state %s' % ' '.join(str(s) for s in initial))
        lbounds = self.get_lower_bounds(dtype=None)
        #lbounds = []
        #for val in  self.eval_parameters():
            #if not isinstance(val.lower, collections.Iterable):
            #    lbounds.extend(val["lower"] for _ in range(val['size']))
            #else:
            #    lbounds.extend(val.lower())
        ubounds = self.get_upper_bounds(dtype=None)
        #ubounds = []
        #for val in  self.eval_parameters():
        #for val in self._desvars.values():
            #if not isinstance(val["upper"], collections.Iterable):
            #    ubounds.extend(val["upper"]  for _ in range(val['size']))
            #else:
        #        ubounds.extend(val.upper())
        self.input.reg_variables.extend([
            '  lower_bounds %s' % ' '.join(str(bnd) for bnd in lbounds),
            '  upper_bounds %s' % ' '.join(str(bnd) for bnd in ubounds)])
        if lbounds and state_params:
               self.input.state_variables.extend([
            '  lower_bounds %s' % ' '.join(str(bnd) for bnd in lbounds),
            '  upper_bounds %s' % ' '.join(str(bnd) for bnd in ubounds)])

        names = [s[0] for s in parameters]
        self.input.reg_variables.append(
            '  descriptors  %s' % ' '.join("'" + str(nam) + "'" for nam in names))
        if names and state_params:
            self.input.state_variables.append(
            '  descriptors  %s' % ' '.join("'" + str(nam) + "'" for nam in names))

        # Add special distributions cases
        for var in self.special_distribution_variables:
            if var in parameters: self.remove_parameter(var)
            if ']' in var:
               if int(re.findall("(.*)\[(.*)\]", var)[0][1])==0 and re.findall("(.*)\[(.*)\]", var)[0][0] not in self._desvars.keys():
                   self.add_parameter(re.findall("(.*)\[(.*)\]", var)[0][0])
            else: self.add_parameter(var, low=-99999999., high=99999999.)
        if self.normal_descriptors:
            # print(self.normal_means) ; quit()
            self.input.uncertain_variables.extend([
                'normal_uncertain =  %s' % len(self.normal_means),
                '  means  %s' % ' '.join(self.normal_means),
                '  std_deviations  %s' % ' '.join(self.normal_std_devs),
                "  descriptors  '%s'" % "' '".join(self.normal_descriptors),
                '  lower_bounds = %s' % ' '.join(self.normal_lower_bounds),
                '  upper_bounds = %s' % ' '.join(self.normal_upper_bounds)
            ])
        if self.lognormal_descriptors:
            self.input.uncertain_variables.extend([
                'lognormal_uncertain = %s' % len(self.lognormal_means),
                '  means  %s' % ' '.join(self.lognormal_means),
                '  std_deviations  %s' % ' '.join(self.lognormal_std_devs),
                "  descriptors  '%s'" % "' '".join(self.lognormal_descriptors)
            ])
        if self.exponential_descriptors:
            self.input.uncertain_variables.extend([
                'exponential_uncertain = %s' % len(self.exponential_descriptors),
                '  betas  %s' % ' '.join(self.exponential_betas),
                "  descriptors ' %s'" % "' '".join(self.exponential_descriptors)
            ])
        if self.beta_descriptors:
            self.input.uncertain_variables.extend([
                'beta_uncertain = %s' % len(self.beta_descriptors),
                '  betas = %s' % ' '.join(self.beta_betas),
                '  alphas = %s' % ' '.join(self.beta_alphas),
                "  descriptors = '%s'" % "' '".join(self.beta_descriptors),
                '  lower_bounds = %s' % ' '.join(self.beta_lower_bounds),
                '  upper_bounds = %s' % ' '.join(self.beta_upper_bounds)
            ])
        if self.gamma_descriptors:
            self.input.uncertain_variables.extend([
                'beta_uncertain = %s' % len(self.gamma_descriptors),
                '  betas = %s' % ' '.join(self.gamma_betas),
                '  alphas = %s' % ' '.join(self.gamma_alphas),
                "  descriptors = '%s'" % "' '".join(self.gamma_descriptors)
            ])
        if self.weibull_descriptors:
            self.input.uncertain_variables.extend([
                'weibull_uncertain = %s' % len(self.weibull_descriptors),
                '  betas  %s' % ' '.join(self.weibull_betas),
                '  alphas  %s' % ' '.join(self.weibull_alphas),
                "  descriptors  '%s'" % "' '".join(self.weibull_descriptors)
            ])


        # CONFIGURE VARIABLES, METHOD, MODEL
        for i in range(len(self.input.responses)):
            if i !=0: self.input.variables.append('\nvariables\n')
            self.input.variables.append("id_variables = 'vars%d'"%(i+1))
            if 'variable_options' in self.input.responses[i]:
               self.input.variables.append(self.input.responses[i]['variable_options'])
               del self.input.responses[i]['variable_options']
            if 'var_types' not in self.input.responses[i]:
               if 'objective_functions' in self.input.responses[i]:
                   self.input.variables.append("\n".join(self.input.reg_variables))
               elif 'response_functions' in self.input.responses[i]:
                   self.input.variables.append("\n".join(self.input.uncertain_variables + self.input.state_variables))
               else: raise ValueError("could not find response or objective in repsonse block %d %s")%(i, '\n'.join(self.input.responses[i]))
            else:
               for vartype in self.input.responses[i]['var_types']:
                   if vartype=='uncertain':
                     self.input.variables.append("\n".join(self.input.uncertain_variables))
                   elif vartype=='design':
                     self.input.variables.append("\n".join(self.input.reg_variables))
                   elif vartype=='state':
                     self.input.variables.append("\n".join(self.input.state_variables))
                   elif vartype=='custom':
                     if self.custom_variables_blocks[i]: self.input.variables.append("\n".join(self.custom_variables_blocks[i]))
                     else: raise ValueError("variable_block not specified but custom variables requested")
                   else: raise ValueError("%s variable type is not supported"%vartype)
               del self.input.responses[i]['var_types']
                       
        objectives = self.get_objectives()
        temp_list = []
        for i in range(len(self.input.method)):
          for key in self.input.method[i]:
                temp_list.append("%s  %s"%(key, self.input.method[i][key]))
        self.methods = temp_list
        self.input.method = temp_list

        self.input.environment.append("method_pointer 'meth1'")

        # Deal with variable mapping
        #cons = []
        #for con in self.get_constraints():
        #    for c in self.get_constraints()[con]:
        #        cons.append(-1 * c)
        cons = []

        secondary_responses = [[0] + [0 for _ in range(len(cons))] for __ in range(len(cons))]
        j = 0
        for i in range(len(cons)):
            secondary_responses[i][j + 1] = 1
            j += 1
        notnormps = [p[0] for p in parameters]
        for x in self.reg_params:
            if x[0] in notnormps: notnormps.remove(x[0])
        names = [s[0] for s in parameters]
        conlist = []
        #for c in self.get_constraints():
        #    conlist.extend(self.get_constraints()[c])
        temp_list = []
        vm = None
        for i in range(len(self.input.model)):
          for key in self.input.model[i]:
                temp_list.append("%s  %s"%(key, self.input.model[i][key]))
                if key == 'nested':
                        vect = [0] *( self.input.n_objectives[i] + len(cons))
                        maps = []
                        for j in range(self.input.n_objectives[i]):
                            s = vect
                            s[j] = 1
                            maps.append(s)
                        if "primary_response_mapping" not in self.input.model[i]:
                            vm = "primary_response_mapping "+\
                             "\n".join(" ".join(" ".join([str(a), str(a)]) for a in  s) for s in maps)
                        else: vm = " "
                if vm:
                   temp_list.append(vm)
                   if "primary_variable_mapping" not in self.input.model[i]: temp_list.append("primary_variable_mapping %s"%" ".join("'" + str(nam) + "'" for nam in names))
                   if cons: 
                       if "secondary_response_mapping" not in self.input.model[i]:
                            temp_list.append("secondary_response_mapping \n%s" % " \n".join( " ".join( " ".join([str(s), str(s)]) for s in secondary_responses[i]) for i in range(len(cons))))
                   if "secondary_variable_mapping" in self.input.model[i] and self.input.model[i]["secondary_variable_mapping"]=="":
                       del self.input.model[i]["secondary_variable_mapping"]
                       temp_list.append("secondary_variable_mapping %s"%" ".join("'mean'" if nam in self.special_distribution_variables else "''" for nam in names))
                   vm = 0
        self.input.model = temp_list
        temp_list = []
        for i in range(len(self.input.responses)):
            if 'objective_functions' in self.input.responses[i]:
                self.input.responses[i]['nonlinear_inequality_constraints'] = len(cons)
            if 'response_functions' in self.input.responses[i]:
                self.input.responses[i]["response_functions"] = self.input.n_objectives[i] + len(cons)
            for key in self.input.responses[i]:
                #temp_list.append(key)
                if self.input.responses[i][key] or self.input.responses[i][key]==0:
                    temp_list.append(str(key) + '  '+str(self.input.responses[i][key]))
                else: temp_list.append(key)
        self.input.responses = temp_list

        self.configured = 1

    # This is the entry point to initialize the analysis run
    def execute(self):
        """ Write DAKOTA input and run. """
        self.configure_input() 
        #self._prob = problem
        #if not self.configured: self.configure_input(problem) # this limits configuration to one time
        self.run_dakota()

# ---------------------------  special distribution magic ---------------------- #
 
    def clear_special_variables(self):
       for var in self.special_distribution_variables:
          try: self.remove_parameter(var)
          except AttributeError:
             pass
       self.special_distribution_variables = []

       self.normal_means = []
       self.special_distribution_variables = []

       self.normal_means = []
       self.normal_std_devs = []
       self.normal_descriptors = []
       self.normal_lower_bounds = []
       self.normal_upper_bounds = []
   
       self.lognormal_means= []
       self.lognormal_std_devs = []
       self.lognormal_descriptors = []
   
       self.exponential_betas = []
       self.exponential_descriptors = []
   
       self.beta_betas = []
       self.beta_alphas = []
       self.beta_descriptors = []
       self.beta_lower_bounds = []
       self.beta_upper_bounds = []

       self.gamma_alphas = []
       self.gamma_betas = []
       self.gamma_descriptors = []

       self.weibull_alphas = []
       self.weibull_betas = []
       self.weibull_descriptors = []

    # adds a probability variable. This concept is unique to pydakdriver.
    def add_special_distribution(self, var, dist, alpha = _SET_AT_RUNTIME, beta = _SET_AT_RUNTIME, 
                                 mean = _SET_AT_RUNTIME, std_dev = _SET_AT_RUNTIME,
                                 lower_bounds = _SET_AT_RUNTIME, upper_bounds = _SET_AT_RUNTIME ):
        def check_set(option):
            if option == _SET_AT_RUNTIME: raise ValueError("INCOMPLETE DEFINITION FOR VARIABLE "+str(var))

        varlist = [] # handles array entries
        if dist == 'normal':
            check_set(std_dev)
            check_set(mean)
           # check_set(lower_bounds)
           # check_set(upper_bounds)
          #  self.normal_lower_bounds.append(str(lower_bounds))
          #  self.normal_upper_bounds.append(str(upper_bounds))
            if True:#str(type(mean)) in ['int', 'str']:
               self.normal_means.append(str(mean))
               self.normal_std_devs.append(str(std_dev))
               self.normal_descriptors.append(var)
               self.normal_lower_bounds.append(str(lower_bounds))
               self.normal_upper_bounds.append(str(upper_bounds))
            else:
               self.normal_means.extend(str(m) for m in mean)
               self.normal_std_devs.extend(str(s) for s in std_dev)
               for i in range(len(mean)): 
                   self.normal_descriptors.append(var+"[%d]"%i)
                   varlist.append(var+"[%d]"%i)
               self.normal_lower_bounds.extend(str(l) for l in lower_bounds)
               self.normal_upper_bounds.extend(str(u) for u in upper_bounds)
               
               
        elif dist == 'lognormal':
            check_set(std_dev)
            check_set(mean)
            self.lognormal_means.append(str(mean))
            self.lognormal_std_devs.append(str(std_dev))
            self.lognormal_descriptors.append(descriptor)
               
        elif dist == 'exponential':
            check_set(beta)
            check_set(descriptor)
            self.exponential_betas.append(str(beta))
            self.exponential_descriptors.append(descriptor)

        elif dist == 'beta':
            check_set(beta)
            check_set(alpha)
            check_set(lower_bounds)
            check_set(upper_bounds)

            self.beta_betas.append(str(beta))
            self.beta_alphas.append(str(alpha))
            self.beta_descriptors.append(var)
            self.beta_lower_bounds.append(str(lower_bounds))
            self.beta_upper_bounds.append(str(upper_bounds))
            
        elif dist == "gamma":
            check_set(beta)
            check_set(alpha)

            self.gamma_alphas.append(str(alpha))
            self.gamma_betas.append(str(beta))
            self.gamma_descriptors.append(var)

            self.weibull_betas.append(str(beta))
            self.weibull_descriptors.append(var)
       
        else: 
            raise ValueError(str(dist)+" is not a defined distribution")

        if varlist:
          for var in varlist:
            self.special_distribution_variables.append(var)
        else:
            self.special_distribution_variables.append(var)

################################################################################
########################## Hierarchical Driver ################################
class pydakdriver(DakotaBase):
    implements(IOptimizer) # Not sure what this does

    def __init__(self, name=None, comm=None):
        self.other_model = None
        self.custom_variables_blocks = []
        super(pydakdriver, self).__init__()
        #self.input.method = collections.OrderedDict()
        #self.input.responses = collections.OrderedDict()
        if comm: self.mpi_comm = comm
        else: self.mpi_comm = None
        self.input.uncertain_variables = []
        self.input.state_variables = []
        self.methods = []
        self.input.model = []
        self.input.reg_variables = []
        self.input.n_objectives = []

        # default definitions for set_variables
        self.ouu = False
        self.stdMult = 1.
        self.meanMult = 1.
        self.need_start = False 
        self.uniform = False
        self.need_bounds = True

        self.dakota_hotstart = False
        # allow arrays to be desvars
        self.array_desvars = []

        self.n_sub_samples = 50
        self.n_sur_samples = 50
        self.max_function_evaluations = '999000'
        self.constraint_tolerance = 1e-8
        self.population_size = 100
        self.seed = 123
        self.convergence_tolerance = '1.e-8'
        self.max_iterations = 2000
        self.fd_gradient_step_size = '1e-8'
        self.final_solutions = 8

 
        if name: self.name = name
        else: self.name = 'dakota_'+str(id(self))

    # How DAKOTA input file options are set:
    #    1. user sets options either using function calls or setting objects 
    #            The design allows both options to have the same effect
    #            (These functions are shown below)
    #    2. When the driver is run(), the self.input objects are searched and used to build input file
    #            (This code is above)
    #    - The items in self.input are stored as orderedDict so order matters
    #    - If an object has '' as it's value, There is no corresponding value it is just a command (eg. no_gradients)
    #    - If an object has _SET_AT_RUNTIME as it's value, then the user must set this. _SET_AT_RUNTIME is a placeholder
    #      which allows the value to be set until runtime. if a key is associated with a value besides '' 
    #      or _SET_AT_RUNTIME. the value is effectively hardwired.


    # REASONING BEHIND ADD_METHOD
    # The conception is that each method is associated with model, responses, and variables specifications. The user can specify to associate the method with different variable/response/model blocks, but the # response blocks = # method blocks = # responses blocks = # method blocks, even if some go unused. This allows for the interface to be intuitive and flexible.
    # In the event that a desired input file can't be made, we suggest writing your own configure_input method, or even just calling the run_dakota method directly.
    #
    # Assumptions
    # -------------
    #1. variable types must be specified, but variable blocks of specific types will always be the same (e.g., we can't have two uncertain variables blocks with different specifications)
    #
    # Tips
    # -----
    # specifying secondary_variable_mapping as '' in model_options defgaults to mapping continous_design to associated uncertain or state variables

    def add_method(self, method='conmin frcg', method_options={}, model='single', model_options={}, uq_responses=None, variable_mapping=None, variables_pointer=1, responses_pointer=1, model_pointer=1, method_id = None, dace_method_pointer=None, response_options = {}, variable_block=None,
                   response_type='o', gradients=False, hessians=False, n_objectives = 1, obj_mult=None, variable_types=[], variable_options=None):
        self.input.method.append(collections.OrderedDict())
        self.input.model.append(collections.OrderedDict())
        self.input.responses.append(collections.OrderedDict())

        # method
        self.custom_variables_blocks.append( variable_block)
        if len(self.input.method) != 1: self.input.method[-1]['method'] = ''
        if type(model_pointer)=='str': self.input.method[-1]['model_pointer'] = "'%s'"%model_pointer
        elif model_pointer: self.input.method[-1]['model_pointer'] = "'mod%d'"%len(self.input.model)
        if method_id: self.input.method[-1]['id_method'] = "'%s'"%method_id
        else: self.input.method[-1]['id_method'] = "'meth%d'"%len( self.input.method)
        self.input.method[-1][method] = ''
        for opt in method_options: self.input.method[-1][opt] = method_options[opt]

        # model
        if len(self.input.method) != 1: self.input.model[-1]['model'] = ''
        self.input.model[-1]["id_model"] = "'mod%d'"%len(self.input.model)
        self.input.model[-1][model] = ''
        if obj_mult:
            if len(obj_mult)!=n_objectives:
                raise ValueError("obj_mult must be same length as n_objectives %d %s"%(n_objectives,
                             ' '.join(str(s) for s in obj_mult)))
            else: self.input.obj_mult = obj_mult
        # TODO: self.input.n_objectives should be an array with one value per method
        for opt in model_options: self.input.model[-1][opt] = model_options[opt]
        if responses_pointer:
            if isinstance(responses_pointer,str): self.input.model[-1]['responses_pointer'] = "'%s'"%responses_pointer
            else: self.input.model[-1]['responses_pointer'] = "'resp%d'"%len(self.input.model)
        if variables_pointer:
            if isinstance(variables_pointer,str): self.input.model[-1]['variables_pointer'] = "'%s'"%variables_pointer
            else: self.input.model[-1]['variables_pointer'] = "'vars%d'"%len(self.input.model)
        if model == 'nested':
            self.input.model[-1]["sub_method_pointer"] = "'meth%d'"%(len(self.input.model)+1)
        if model == 'surrogate':
            #del self.input.model[-1]['variables_pointer']
            if dace_method_pointer: self.input.model[-1]["dace_method_pointer"] = "'%s'"%dace_method_pointer
        self.input.n_objectives.append(n_objectives)

        # responses
        if len(self.input.method) != 1: self.input.responses[-1]["responses"]=''
        self.input.responses[-1]["id_responses"] = "'resp%d'"%len(self.input.model)
        if response_type=='o':
            self.input.responses[-1]["objective_functions"] = self.input.n_objectives[-1] if not uq_responses else uq_responses
        elif response_type=='r':
            self.input.responses[-1]["response_functions"] = self.input.n_objectives[-1]
        for r in response_options: self.input.responses[-1][r] = response_options[r]
        if not gradients: self.input.responses[-1]["no_gradients"] = ''
        elif gradients == 'analytic':
            self.input.responses[-1]['analytic_gradients'] = ''
        elif gradients == 'numerical':
            self.input.responses[-1]['numerical_gradients'] = ''
            self.input.responses[-1]['method_source dakota'] = ''
            self.input.responses[-1]['interval_type'] = ''
            self.input.responses[-1]['fd_gradient_step_size'] = self.fd_gradient_step_size
        else: raise ValueError("Gradient specification '%s' is not 'analytic' or 'numerical'"%gradients)
        if not hessians:  self.input.responses[-1]["no_hessians"] = ''
        if variable_types: self.input.responses[-1]['var_types'] = variable_types # we will translate this to the variables section in configure_input
        if variable_options: self.input.responses[-1]['variable_options'] = variable_options
    def analytical_gradients(self):
         self.interval_type = 'forward'
         for key in self.input.responses:
             if key == 'no_gradients':
                  self.input.responses.pop(key)
         self.input.responses['numerical_gradients'] = ''
         self.input.responses['method_source dakota'] = ''
         self.input.responses['interval_type'] = ''
         self.input.responses['fd_gradient_step_size'] = self.fd_gradient_step_size

    def numerical_gradients(self, method_source='dakota'):
         for key in self.input.responses:
             if key == 'no_gradients': self.input.responses.pop(key)
         self.input.responses['numerical_gradients'] = ''
         if method_source=='dakota':self.input.responses['method_source dakota']=''
         self.interval_type = 'forward'
         self.input.responses['interval_type'] = ''
         self.input.responses['fd_gradient_step_size'] = self.fd_gradient_step_size

    def hessians(self):
         self.input.responses['numerical_hessians']=''
         for key in self.input.responses:
             if key == 'no_hessians':
                  self.input.responses.pop(key)
         # todo: Create Hessian default with options

    def Optimization(self,opt_type='optpp_newton', interval_type = 'forward', surrogate_model=False, ouu=False, compromise=False, sub_sample_type='polynomial_chaos' ):
        self.input.method["id_method"] = "'opt'"
        self.input.responses['objective_functions']=_SET_AT_RUNTIME
        cons = self.get_constraints()
        write_res = True
        if compromise: self.compromise = True
        else: self.compromise = False
        if ouu: self.sub_sample_type = sub_sample_type
        conlist = []
        for c in cons:
           conlist.extend(cons[c])
        self.input.responses['nonlinear_inequality_constraints']=len(conlist)
        #self.input.responses['nonlinear_inequality_upper_bounds']="%s"%(' '.join(".1" for _ in range(len(conlist))))
        if opt_type == 'optpp_newton':
            self.need_start=True
            self.need_bounds=True
            self.input.method[opt_type] = ""
            self.analytical_gradients()
            self.hessians()
        if opt_type == 'moga':
            self.input.method["id_method"] = "'opt'"
            self.input.method[opt_type] = ""
            self.input.method["output"] = "silent"
            self.input.method["final_solutions"] = self.final_solutions
            self.input.method["population_size"] = self.population_size
            self.input.method["max_iterations"] = self.max_iterations
            self.input.method["max_function_evaluations"] = self.max_function_evaluations
            self.input.method["replacement_type"] = "unique_roulette_wheel"
        if opt_type == 'soga':
            #if ouu: self.input.method["moga"] = ""
            #else: self.input.method[opt_type] = ""
            self.input.method[opt_type] = ""
            self.input.method["convergence_type"] = "\taverage_fitness_tracker"
            self.input.method["population_size"] = self.population_size
            self.input.method["max_iterations"] = self.max_iterations
            self.input.method["max_function_evaluations"] = self.max_function_evaluations
            self.input.method["replacement_type"] = "unique_roulette_wheel"
        if opt_type == 'efficient_global':
            self.input.method["efficient_global"] = ""
            self.input.method["seed"] = _SET_AT_RUNTIME
            self.numerical_gradients()
            self.input.method["max_function_evaluations"] = self.max_function_evaluations
            self.input.method["replacement_type"] = "unique_roulette_wheel"
        if opt_type == 'efficient_global':
            self.input.method["efficient_global"] = ""
            self.input.method["seed"] = _SET_AT_RUNTIME
            self.numerical_gradients()
        if opt_type == 'conmin':
            self.need_start=True           

            self.input.method[opt_type] = "\t"
            self.input.method['constraint_tolerance'] = '1.e-8'
            write_res = False
            self.numerical_gradients()

        if ouu: 
            self.ouu = True
            
            self.input.method["model_pointer"] = "'f1dacem'"
            #self.input.method["model_pointer"] = "'f1m'" 
         
            if self.sub_sample_type == 'polynomial_chaos':
               self.input.method["method\n\tid_method 'expf2'\n\tpolynomial_chaos\n\t\toutput silent\n\t\tsamples %d\n\tsample_type lhs\n\tmodel_pointer 'f2m'\n\tcollocation_ratio 2\n\texpansion_order 2\n"%self.n_sub_samples] = ''
               #self.input.method["method\n\tid_method 'f1dace'\n\tsampling\n\tsample_type lhs\n\toutput silent\n\n\tsamples %d\n\tmodel_pointer 'f1dacem'\n"%self.n_sur_samples] = ''
            else:
               self.input.method["method\n\tid_method 'expf2'\n\tsampling\n\t\toutput silent\n\t\tsamples %d\n\tsample_type lhs\n\tmodel_pointer 'f2m'\n"%self.n_sub_samples] = ''
               #self.input.method["method\n\tid_method 'f1dace'\n\tsampling\n\tsample_type lhs\n\toutput silent\n\n\tsamples %d\n\tmodel_pointer 'f1dacem'\n"%self.n_sur_samples] = ''
               

            #self.input.model = ["  id_model 'f4m'\n  nested\n    sub_method_pointer 'expf3'\n  variables_pointer 'x1only'\n  responses_pointer 'f4r'\n  primary_response_mapping 1 1\n\nmodel\n  id_model 'f3m'\n    surrogate global kriging surfpack\n  variables_pointer 'x1statex2'\n  responses_pointer 'f3r' \n  dace_method_pointer 'f3dace'\n\nmodel\n  id_model 'f3dacem'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f3r'  \n  interface_pointer 'pydak'"]

            #self.input.model = ["  id_model 'f4m'\n  nested\n    sub_method_pointer 'expf3'\n  variables_pointer 'x1only'\n  responses_pointer 'f4r'\n  primary_response_mapping 1 0\n\nmodel\n  id_model 'f3m'\n    surrogate global kriging surfpack\n  variables_pointer 'x1statex2'\n  responses_pointer 'f3r' \n  dace_method_pointer 'f3dace'\n\nmodel\n  id_model 'f3dacem'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f3r'  \n  interface_pointer 'pydak'"]
        if write_res: 
            self.input.responses['no_gradients'] = ''
        self.input.responses['no_hessians'] = '' 
    def Parameter_Study(self,study_type = 'vector'):
        self.study_type = study_type
        if study_type == 'vector':
            self.need_start=True
            self.need_bounds=False
            # why was this false? legacy was self.set_variables(need_start=False, need_bounds=False)
            self.input.method['vector_parameter_study'] = ""
            self.input.method['final_point'] = _SET_AT_RUNTIME 
            self.input.method['num_steps'] = _SET_AT_RUNTIME 
            self.final_point = _SET_AT_RUNTIME
            self.num_steps = _SET_AT_RUNTIME
        if study_type == 'multi-dim':
            self.need_start=False
            self.input.method['multidim_parameter_study'] = ""
            self.input.method['partitions'] = _SET_AT_RUNTIME 
            self.partitions =  _SET_AT_RUNTIME
        if study_type == 'list':
            self.input.method['list_parameter_study'] = ""
            self.input.method['list_of_points'] = _SET_AT_RUNTIME 
            self.input.responses['response_functions']=_SET_AT_RUNTIME
        else: self.input.responses['objective_functions']=_SET_AT_RUNTIME 
        #if study_type == 'centered':
    def UQ(self,UQ_type = 'sampling', use_seed=False):
            self.sample_type =  'random' #'lhs'
            #self.seed = _SET_AT_RUNTIME
            self.samples=100
            
            if UQ_type == 'fsu_quasi_mc':
                self.input.method['fsu_quasi_mc'] = 'halton'
                self.input.method['latinize'] = ''
                self.input.method['samples'] = self.samples
                self.input.responses['num_response_functions'] = _SET_AT_RUNTIME
            if UQ_type == 'stoch_collocation':
                self.input.method['stoch_collocation'] = ''
                self.input.method['sparse_grid_level'] = 3
                self.input.responses['num_response_functions'] = _SET_AT_RUNTIME
            if UQ_type == 'polynomial_chaos':
                self.input.responses['num_response_functions'] = _SET_AT_RUNTIME
                self.input.method['polynomial_chaos'] = ''
                self.input.method['quadrature_order'] = 10
                #self.input.method['samples'] = 1000
                #self.input.method['variance_based_decomp'] = ''
                #self.input.method['sample_type'] = _SET_AT_RUNTIME
                #self.input.method['orthogonal_least_interpolation'] = '10' 
            if UQ_type == 'sampling':
                self.need_start = False
                self.uniform = True
                self.input.method = collections.OrderedDict()
                self.input.method['sampling'] = ''
                self.input.method['output'] = _SET_AT_RUNTIME
                self.input.method['sample_type'] = _SET_AT_RUNTIME
                if use_seed==True: self.input.method['seed'] = _SET_AT_RUNTIME
                self.input.method['samples'] = _SET_AT_RUNTIME
        
                self.input.responses = collections.OrderedDict()
                self.input.responses['num_response_functions'] = _SET_AT_RUNTIME
                self.input.responses['response_descriptors'] = _SET_AT_RUNTIME
            self.input.responses['no_gradients'] = ''
            self.input.responses['no_hessians'] = ''
################################################################################

