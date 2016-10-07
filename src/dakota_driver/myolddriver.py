"""
A collection of drivers using DAKOTA to exercise the workflow.
The general scheme is to have a separate class for each separate DAKOTA
method type.

Currently these drivers simply run the workflow, they do not parse any
DAKOTA results.
"""
from openmdao.util.record_util import create_local_meta
from numpy import array
from mpi4py.MPI import COMM_WORLD as world
import collections

from dakota import DakotaInput, run_dakota
from six import iteritems, itervalues

from openmdao.drivers.predeterminedruns_driver import PredeterminedRunsDriver
from openmdao.core.driver import Driver 
from openmdao.util.record_util import create_local_meta, update_local_meta
#import sys
#from openmdao.main.hasparameters import HasParameters
#from openmdao.main.hasconstraints import HasIneqConstraints
#from openmdao.main.hasobjective import HasObjectives
#from openmdao.main.interfaces import IHasParameters, IHasIneqConstraints, \
#                                     IHasObjectives, IOptimizer, implements
#from openmdao.util.decorators import add_delegate

__all__ = ['DakotaCONMIN', 'DakotaMultidimStudy', 'DakotaVectorStudy',
           'DakotaGlobalSAStudy', 'DakotaOptimizer', 'DakotaBase']

_SET_AT_RUNTIME = "SPECIFICATION DECLARED BUT NOT DEFINED"


#@add_delegate(HasParameters, HasObjectives)
#class DakotaBase(PredeterminedRunsDriver):
class DakotaBase(Driver):
    """
    Base class for common DAKOTA operations, adds :class:`DakotaInput` instance.
    The ``method`` and ``responses`` sections of `input` must be set
    directly.  :meth:`set_variables` is typically used to set the ``variables``
    section.
    """

#    implements(IHasParameters, IHasObjectives)

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
        #parameters = self.get_parameters()
        parameters = self._desvars
        if not parameters:
            self.raise_exception('No parameters, run aborted', ValueError)

        if not self.input.method:
            self.raise_exception('Method not set', ValueError)
        if not self.input.variables:
            self.raise_exception('Variables not set', ValueError)
        if not self.input.responses:
            self.raise_exception('Responses not set', ValueError)

        if self.ouu: 

            conlist = []
            cons = self.get_constraints()
            for c in cons:
               conlist.extend(cons[c])
            resline = self.input.responses[0].split()
            resline[0] = 'response_functions'
            #resline[2] = str( 1 )
            resline[2] = str( 1 + len(conlist) )

            self.input.responses = [" id_responses 'f2r'"] + ['\n'.join(resline)] + ['\n'] + ['\n'.join(['no_gradients', 'no_hessians'])] + ["\nresponses\n  id_responses 'f1r'"] + self.input.responses

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
        from openmdao.core.mpi_wrap import MPI
        if MPI:
            run_dakota(infile, use_mpi=True, stdout=self.stdout, stderr=self.stderr, restart=self.dakota_hotstart)
        else:
            run_dakota(infile, stdout=self.stdout, stderr=self.stderr, restart= self.dakota_hotstart)
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
        #self._logger.debug('cv %s', cv)
        #self._logger.debug('asv %s', asv)

        # support list OR numbers as desvars
        if self.ouu: dvlist = self.special_distribution_variables
        else: dvlist = []
        if self.array_desvars:
            for i, var  in enumerate(dvlist + self.array_desvars):
                self.set_desvar(var, cv[i])
        else:
            dvl = dvlist + self._desvars.keys()
            for i  in range(len(cv)):
                self.set_desvar(dvl[i], cv[i])
        #self.set_parameters(cv)
        #self.run_iteration()
        system = self.root
        metadata = self.metadata  = create_local_meta(None, 'pydakrun%d'%world.Get_rank())
        system.ln_solver.local_meta = metadata
        self.iter_count += 1
        update_local_meta(metadata, (self.iter_count,))
        #with system._dircontext:
            #system.apply_nonlinear(self)
            #self._objfunc()
            #system.problem.run()
            #print('solving nonlinear')
        self.root.solve_nonlinear()

            #system.solve_nonlinear(metadata=metadata)
        #self.recorders.record_iteration(system, metadata)

        #expressions = self.get_objectives().values()[0].tolist()#.update(self.get_constraints())
        #cons = self.get_constraints()
        #for c in cons:
        #       #expressions.append(-1*c)
        #       expressions.append(-1*self.get_constraints()[con])

        expressions = self.get_objectives().values()[0].tolist()#.update(self.get_constraints())
        for con in self.get_constraints().values():
            for c in con:
               expressions.append(-1*c)

        #if hasattr(self, 'get_eq_constraints'):
        #    expressions.extend(self.get_eq_constraints().values()) # revisit - won't work with ordereddict
        #if hasattr(self, 'get_ineq_constraints'):
        #    expressions.extend(self.get_ineq_constraints().values())

        fns = []
        fnGrads = []
        #print 'ASV: ', asv
        #print 'expressions: ',expressions

        for i in range(len(asv)):
        #for i, val in enumerate(expressions.values()):
            val = expressions[i]

            #fns.extend([val])
            #if self.ouu:
            #    fns.extend([a for a in expressions])
            #else:
            if asv[i] & 1 or asv[i]==0:
               fns.extend([val])
            if asv[i] & 2:
            #val = expr.evaluate_gradient(self.parent)
               fnGrads.extend([val])
            #fnGrads.append([val])
            # self.raise_exception('Gradients not supported yet',
            #                      NotImplementedError)
            if asv[i] & 4:
               self.raise_exception('Hessians not supported yet',
                                     NotImplementedError)

        retval = dict(fns=array(fns), fnGrads = array(fnGrads))
       # print 'asv was ',asv
       # print 'returning ',retval
        #self._logger.debug('returning %s', retval)
        return retval


    def configure_input(self, problem):
        """ Configures input specification, must be overridden. """

        ######## 
       # method #
        ######## 
      for i in range(len(self.input.methods)):
        n_params = len(self._desvars.keys())
        #if hasattr(self, 'get_ineq_constraints'): ineq_constraints = self.total_ineq_constraints()
        if hasattr(self, 'get_constraints'): ineq_constraints = self.get_constraints()
        else: ineq_constraints = False
        for key in self.input.methods[i]:
            self.input.method[key] = get_attr(self,key)

        ########### 
       # variables #
        ########### 
        self.set_variables(need_start=self.need_start,
                           uniform=self.uniform,
                           need_bounds=self.need_bounds)
        ########### 
       # responses #
        ########### 
        objectives = self.get_objectives()
        for key in self.input.responses[i]:
            if not self.ouu:
               if key =='objective_functions': self.input.responses[key] = len(objectives)
               if key =='response_functions': self.input.responses[key] = len(objectives)
            else:
               if key =='objective_functions': 
                  if self.compromise: 
                      self.input.responses[key] = 1
                  else:
                      self.input.responses[key] = 2
            if key == 'nonlinear_inequality_constraints' :
                conlist = []
                cons = self.get_constraints()
                #for cons in self.get_constraints().values():
                for c in cons:
                     conlist.extend(cons[c])
                if conlist:  
                      self.input.responses['nonlinear_inequality_constraints']=len(conlist)
                      if self.ouu: self.input.responses['nonlinear_inequality_upper_bounds']="%s"%(' '.join(".1" for _ in range(len(conlist))))
                       
                else: self.input.responses['nonlinear_inequality_constraints']='0'
            #   if ineq_constraints: self.input.responses[key] = ineq_constraints
            #   else: self.input.responses.pop(key)
            if key == 'interval_type': 
               self.input.responses = collections.OrderedDict([(key+' '+self.interval_type, v) if k == key else (k, v) for k, v in self.input.responses.items()])
            if key == 'fd_gradient_step_size': self.input.responses[key] = self.fd_gradient_step_size

            if key == 'num_response_functions': self.input.responses[key] = len(objectives)
            if key == 'response_descriptors': 
                names = ['%r' % name for name in objectives.keys()]
                self.input.responses[key] = ' '.join(names)

        ##################################################
       # Verify that all input fields have been adressed #
        ##################################################
        def assignment_enforcemer(tag,val):
             if val == _SET_AT_RUNTIME: raise ValueError(str(tag)+ " NOT DEFINED")
        for key in self.input.method: assignment_enforcemer(key,self.input.method[key])
        for key in self.input.responses: assignment_enforcemer(key,self.input.responses[key])

        #############################################################
       # map method and response from ordered dictionaries to lists  #
       #                                                             #
       # convention is if the value is an empty string there will be #
       #    no equals sign. Otherwise, data will be inoyt to dakota  #
       #    as "{key} = {associated value}"                          #
        #############################################################
        temp_list = []
        for key in self.input.method:
            if self.input.method[key]:
                temp_list.append(str(key) + ' = '+str(self.input.method[key]))
            else: temp_list.append(key)
        self.input.method = temp_list

        temp_list = []
        for key in self.input.responses:
            if self.input.responses[key]:
                temp_list.append(str(key) + ' = '+str(self.input.responses[key]))
            else: temp_list.append(key)
        self.input.responses = temp_list

        self.configured = 1

    #def execute(self):
    def run(self, problem):
        """ Write DAKOTA input and run. """
        if not self.configured: self.configure_input(problem) # this limits configuration to one time
        self.run_dakota()

    def set_variables(self, need_start, uniform=False, need_bounds=True):
        """ Set :class:`DakotaInput` ``variables`` section. """

        dvars = self.get_desvars()
        parameters = [] # [ [name, value], ..]
        self.reg_params = parameters
        for param in dvars.keys():
            if len( dvars[param]) == 1:
                parameters.append( [param, dvars[param][0]])
            else:
                for i, val in enumerate(dvars[param]):
                    parameters.append([param+'['+str(i)+']', val])
                    self.array_desvars.append(param+'['+str(i)+']')
        if parameters:
            if uniform:
                self.input.variables = [
                    'uniform_uncertain = %s' % len(parameters)]
                    #'uniform_uncertain = %s' % self.total_parameters()]
            else:
                self.input.variables = [
                    'continuous_design = %s' % len(parameters)]
                    #'continuous_design = %s' % self.total_parameters()]
    
            if need_start:
                #initial = [str(val[0] for val in self.get_desvars().values()]
                initial = []
                for val in self.get_desvars().values():
                    if isinstance(val, collections.Iterable):
                        initial.extend(val)
                    else: initial.append(val)
                #initial = [str(val) for val in self.eval_parameters(dtype=None)]
                self.input.variables.append(
                    '  initial_point %s' % ' '.join(str(s) for s in initial))
    
            if need_bounds:
                #lbounds = [str(val) for val in self.get_lower_bounds(dtype=None)]
                #ubounds = [str(val) for val in self.get_upper_bounds(dtype=None)]
                lbounds = []
                for val in self._desvars.values():
                    if isinstance(val["lower"], collections.Iterable):
                        lbounds.extend(val["lower"])
                    else: lbounds.append(val["lower"])
                #lbounds = [str(val['lower']) for val in parameters.values()]
                #ubounds = [str(val['upper']) for val in parameters.values()]
                ubounds = []
                for val in self._desvars.values():
                    if isinstance(val["upper"], collections.Iterable):
                        ubounds.extend(val["upper"])
                    else: ubounds.append(val["upper"])
                self.input.variables.extend([
                    '  lower_bounds %s' % ' '.join(str(bnd) for bnd in lbounds),
                    '  upper_bounds %s' % ' '.join(str(bnd) for bnd in ubounds)])
    
            names = [s[0] for s in parameters]
            #names = []
            #for param in parameters.values():
            #    for name in param.names:
            #        names.append('%r' % name)
    
            self.input.variables.append(
                '  descriptors  %s' % ' '.join( "'"+str(nam)+"'" for nam in names)
            )
        

        if self.ouu:
           self.input.environment.append("  method_pointer 'opt'")

#           dvars = self.get_desvars()
#           parameters = [] # [ [name, value], ..]
#           self.reg_params = parameters
#           for param in dvars.keys():
#            if len( dvars[param]) == 1:
#                parameters.append( [param, dvars[param][0]])
#            else:
#                for i, val in enumerate(dvars[param]):
#                    parameters.append([param+'['+str(i)+']', val])
#                    self.array_desvars.append(param+'['+str(i)+']')

           cons = []
           for con in self.get_constraints():
              for c in self.get_constraints()[con]:
                 cons.append(-1*c)


           secondary_responses = [[0] + [0 for _ in range(len(cons))] for __ in range(len(cons))]
           j = 0
           for i in range(len(cons)):
               secondary_responses[i][j+1] = 1
               j+=1

           notnormps = [p[0] for p in parameters]
           for x in self.reg_params:
             if x[0] in notnormps: notnormps.remove(x[0])
           #self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping 1 0\n    primary_variable_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%' '.join( "'"+str(nam)+"'" for nam in [s[0] for s in self.reg_params])]
           #self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping 1 3 %s\n    primary_variable_mapping %s\nsecondary_response_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(' '.join(["0 0" for _ in range(len(cons[0]))]), ' '.join( "'"+str(nam)+"'" for nam in [s[0] for s in self.reg_params]), ' '.join(["1 3" for _ in range(len(cons[0]))]))]
           names = [s[0] for s in parameters]

           #self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f 0\n0 %f \n    primary_variable_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult," ".join("'%s'"%i for i in names))]
           #self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f 0\n0 %f %s\n    primary_variable_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult, " ".join("1 2" for i in range(len(cons))) ," ".join("'%s'"%i for i in names))]
           #self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f 0\n0 %f\n    primary_variable_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult," ".join("'%s'"%i for i in names))]
           if cons:

              if self.compromise:
                  #self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f %f %s\n primary_variable_mapping %s\nsecondary_response_mapping \n%s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult, " ".join(" 0 0 " for _ in range(len(cons))), " ".join("'%s'"%i for i in names), " \n".join( " ".join( " ".join([str(s), str(s)]) for s in secondary_responses[i]) for i in range(len(cons))))]
                  self.input.model = ["  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f %f %s\n primary_variable_mapping %s\nsecondary_response_mapping \n%s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult, " ".join(" 0 0 " for _ in range(len(cons))), " ".join("'%s'"%i for i in names), " \n".join( " ".join( " ".join([str(s), str(s)]) for s in secondary_responses[i]) for i in range(len(cons))))]
              else:
                  self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f %f %s\n0 %f %s\n    primary_variable_mapping %s\nsecondary_response_mapping \n%s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult, " ".join(" 0 0 " for _ in range(len(cons))), self.stdMult, " ".join(" 0 0 " for _ in range(len(cons))), " ".join("'%s'"%i for i in names), " \n".join( " ".join( " ".join([str(s), str(s)]) for s in secondary_responses[i]) for i in range(len(cons))))]
           else:
              if self.compromise:
                  self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f %f\n    primary_variable_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult," ".join("'%s'"%i for i in names))]
              else:
                  self.input.model = ["  id_model 'f1m'\n  surrogate global kriging surfpack\n  dace_method_pointer 'f1dace'\n  variables_pointer 'x1only'\n  responses_pointer 'f1r'\nmodel\n  id_model 'f1dacem'\n   nested\n   variables_pointer 'x1only'\n  responses_pointer 'f1r'\n   sub_method_pointer 'expf2'\n   primary_response_mapping %f 0\n0 %f\n    primary_variable_mapping %s\nmodel\n  id_model 'f2m'\n  single\n  variables_pointer 'x1andx2'\n  responses_pointer 'f2r'\n  interface_pointer 'pydak'"%(self.meanMult, self.stdMult," ".join("'%s'"%i for i in names))]
           varlist = self.input.variables
           #ln = varlist[0].split()
           #ln[0] = 'continuous_state'
           #varlist = [' '.join(ln)] + varlist[1:]
           #blk1 = varlist
           #blk2 = varlist
           blk1 = ["   id_variables 'x1only'"] + varlist
           #blk2 = ["variables\n  active all\n  id_variables 'x1andx2'"] + varlist
           blk3 = ["variables\n  id_variables 'x1andx2'\n "] + ['continuous_state = %s' % len(parameters)]
           #blk3 = ["variables\n  id_variables 'x1andx2'\n "] + ['continuous_design = %s' % len(parameters)]

           #blk3 += [ 'continuous_design = %s\n' % len(parameters)] + ['  descriptors  %s' % ' '.join( "'"+str(nam)+"'" for nam in names]
           self.input.variables = blk1+['\n']+blk3
           #self.input.variables = blk1+['\n']+blk2+['\n']+blk3

           #print '*', self._desvars.values()
           #print '*', self.get_constraint_metadata()
           #print '*', parameters ; quit()

           if need_bounds:
                self.input.variables.extend([
                    '  lower_bounds %s' % ' '.join(str(bnd) for bnd in lbounds),
                    '  upper_bounds %s' % ' '.join(str(bnd) for bnd in ubounds)])

           self.input.variables.append(
                '  descriptors  %s' % ' '.join( "'"+str(nam)+"'" for nam in names))

        # ------------ special distributions cases ------- -------- #
        for var in self.special_distribution_variables:
             if var in parameters: self.remove_parameter(var)
             self.add_desvar(var)#,low= -999, high = 999)
             #self.add_param(var)#,low= -999, high = 999)
             #self.add_parameter(var,low= -999, high = 999)


        if self.normal_descriptors:
            #print(self.normal_means) ; quit()
            self.input.variables.extend([
                'normal_uncertain =  %s' % len(self.normal_means),
                '  means  %s' % ' '.join(self.normal_means),
                '  std_deviations  %s' % ' '.join(self.normal_std_devs),
                "  descriptors  '%s'" % "' '".join(self.normal_descriptors),
                '  lower_bounds = %s' % ' '.join(self.normal_lower_bounds),
                '  upper_bounds = %s' % ' '.join(self.normal_upper_bounds)
                ])
                   
        if self.lognormal_descriptors:
            self.input.variables.extend([
                'lognormal_uncertain = %s' % len(self.lognormal_means),
                '  means  %s' % ' '.join(self.lognormal_means),
                '  std_deviations  %s' % ' '.join(self.lognormal_std_devs),
                "  descriptors  '%s'" % "' '".join(self.lognormal_descriptors)
                ])
                   
        if self.exponential_descriptors:
            self.input.variables.extend([
                'exponential_uncertain = %s' % len(self.exponential_descriptors),
                '  betas  %s' % ' '.join(self.exponential_betas),
                "  descriptors ' %s'" % "' '".join(self.exponential_descriptors)
                ])
                   
        if self.beta_descriptors:
            self.input.variables.extend([
                'beta_uncertain = %s' % len(self.beta_descriptors),
                '  betas = %s' % ' '.join(self.beta_betas),
                '  alphas = %s' % ' '.join(self.beta_alphas),
                "  descriptors = '%s'" % "' '".join(self.beta_descriptors),
                '  lower_bounds = %s' % ' '.join(self.beta_lower_bounds),
                '  upper_bounds = %s' % ' '.join(self.beta_upper_bounds)
                ])

        if self.gamma_descriptors:
            self.input.variables.extend([
                'beta_uncertain = %s' % len(self.gamma_descriptors),
                '  betas = %s' % ' '.join(self.gamma_betas),
                '  alphas = %s' % ' '.join(self.gamma_alphas),
                "  descriptors = '%s'" % "' '".join(self.gamma_descriptors)
                ])

        if self.weibull_descriptors:
            self.input.variables.extend([
                'weibull_uncertain = %s' % len(self.weibull_descriptors),
                '  betas  %s' % ' '.join(self.weibull_betas),
                '  alphas  %s' % ' '.join(self.weibull_alphas),
                "  descriptors  '%s'" % "' '".join(self.weibull_descriptors)
                ])
        

    
# ---------------------------  special distributions ---------------------- #
 
    def clear_special_variables(self):
       for var in self.special_distribution_variables:
          try: self.remove_parameter(var)
          except AttributeError:
             pass
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

        elif dist == "weibull":
            check_set(beta)
            check_set(alpha)

            self.weibull_alphas.append(str(alpha))
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
    #implements(IOptimizer) # Not sure what this does

    def __init__(self, name=None):
        super(pydakdriver, self).__init__()
        self.input.method = collections.OrderedDict()
        self.input.responses = collections.OrderedDict()

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


    def add_method(self, method_type='optimization', method='conmin', response_type=None):

        # whether to use response_functions or objective_functions 
        if not response_type:
            if method_type == 'optimization':
                response_type='o'
            elif method_type =='UQ':
                response_type='r'
        self.input.responses.append(respose_type)
        
        self.methods.append({'method':method})

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
        if study_type == 'centered':
            self.input.method['centered_parameter_study'] = ""
            self.input.method['step_vector'] = _SET_AT_RUNTIME
            self.input.method['steps_per_variable'] = _SET_AT_RUNTIME
        self.input.responses['no_gradients']=''
        self.input.responses['no_hessians']=''

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