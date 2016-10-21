import os,sys, json
import multiprocessing
from time import sleep
import logging
logger = logging.getLogger('luigi-interface')


import luigi
from luigi.contrib.slurm import SlurmExecutableTask
from luigi.util import requires, inherits
from luigi import LocalTarget
from luigi.file import TemporaryFile

picard="java -XX:+UseSerialGC -Xmx{mem}M -jar /tgac/software/testing/picardtools/2.1.1/x86_64/bin/picard.jar"
gatk="java -XX:+UseSerialGC -Xmx{mem}M -jar /tgac/software/testing/gatk/3.6.0/x86_64/bin/GenomeAnalysisTK.jar "
python="source /usr/users/ga004/buntingd/FP_dev/dev/bin/activate"

'''
Guidelines for harmonious living:
--------------------------------
1. Tasks acting on fastq files should output() a list like [_R1.fastq, _R2.fastq]
2. Tasks acting on a bam should just output() a single Target
3. Tasks acting on a vcf should just output() a single Target'''

#-----------------------------------------------------------------------#

class FetchFastqGZ(SlurmExecutableTask):
    '''Fetches and concatenate the fastq.gz files for ``library`` from the /reads/ server
     :param str library: library name  '''
    
    library = luigi.Parameter()
    base_dir = luigi.Parameter(default="/usr/users/ga004/buntingd/FP_dev/testing/", significant=False)
    scratch_dir = luigi.Parameter(default="/tgac/scratch/buntingd/", significant=False)
    read_dir = luigi.Parameter(default="/tgac/data/reads/*DianeSaunders*", significant=False)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 500
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        LocalTarget(os.path.join(self.scratch_dir, self.library, "raw_R1.fastq.gz")).makedirs()
        return [LocalTarget(os.path.join(self.scratch_dir, self.library, "raw_R1.fastq.gz")),
                LocalTarget(os.path.join(self.scratch_dir, self.library, "raw_R2.fastq.gz"))]
    
    def work_script(self):
        return '''#!/bin/bash -e 
        
                  find {read_dir} -name "*{library}*_R1.fastq.gz" -type f | while read fname; do
                      cat < $fname >> {R1}
                  done
                  
                  find {read_dir} -name "*{library}*_R2.fastq.gz" -type f | while read fname; do
                      cat < $fname >> {R2}
                  done
                  
                  sleep 30
                 '''.format(read_dir = self.read_dir,
                            library=self.library,
                            R1=self.output()[0].path,
                            R2=self.output()[1].path)  

@requires(FetchFastqGZ)
class PythonFilter(SlurmExecutableTask):
    '''Applies the python script fasta_filter.py to remove reads containing Ns and reads not exactly 101bp long'''
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 500
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return [LocalTarget(os.path.join(self.scratch_dir, self.library, "pyfilter_R1.fastq.gz")),
                LocalTarget(os.path.join(self.scratch_dir, self.library, "pyfilter_R2.fastq.gz"))]
    
    def work_script(self):
        return '''#!/bin/bash -e 
                {python}
                python fastq_filter.py {R1_in} {R2_in} {R1_out} {R2_out} -L 101
                 '''.format(python=python,
                            R1_in=self.input()[0].path,
                            R2_in=self.input()[1].path,
                            R1_out=self.output()[0].path,
                            R2_out=self.output()[1].path)

@requires(PythonFilter)
class FastxQC(SlurmExecutableTask):
    '''Runs Fastx toolkit to plot the nucleotide and base call quality score distributions '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
      
    def output(self):
        working_dir = os.path.join(self.base_dir, self.library)
        return {'stats_R1': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R1_stats.txt")),
                'stats_R2': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R2_stats.txt")),
                'boxplot_R1': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R1_quality.png")),
                'boxplot_R2': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R2_quality.png")),
                'nt_dist_R1': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R1_nt_distr.png")),
                'nt_dist_R2': LocalTarget(os.path.join(working_dir, 'QC', self.library + "_R2_nt_distr.png")),
            }
    
    def work_script(self):
        return '''#!/bin/bash -e
        source fastx_toolkit-0.0.13.2
        
        gzip -cd {R1_in} | fastx_quality_stats -o {stats_R1} -Q33
        gzip -cd {R2_in} | fastx_quality_stats -o {stats_R2} -Q33

        fastq_quality_boxplot_graph.sh -i {stats_R1} -o {boxplot_R1}
        fastq_quality_boxplot_graph.sh -i {stats_R2} -o {boxplot_R2}
                
        fastx_nucleotide_distribution_graph.sh -i {stats_R1} -o {nt_dist_R1}
        fastx_nucleotide_distribution_graph.sh -i {stats_R2} -o {nt_dist_R2}

        '''.format(R1_in=self.input()[0].path,
                   R2_in=self.input()[1].path,
                   stats_R1=self.output()['stats_R1'].path,
                   stats_R2=self.output()['stats_R2'].path,
                   boxplot_R1=self.output()['boxplot_R1'].path,
                   boxplot_R2=self.output()['boxplot_R2'].path,
                   nt_dist_R1=self.output()['nt_dist_R1'].path,
                   nt_dist_R2=self.output()['nt_dist_R2'].path)

@requires(PythonFilter)
class FastxTrimmer(SlurmExecutableTask):
    '''Uses FastxTrimmer to remove Illumina adaptors and barcodes'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
        
    def output(self):
        working_dir = os.path.join(self.base_dir, self.library)
        return [LocalTarget(os.path.join(self.scratch_dir, self.library, "filtered_R1.fastq.gz")),
                LocalTarget(os.path.join(self.scratch_dir, self.library, "filtered_R2.fastq.gz"))]
    
    def work_script(self):
        return '''#!/bin/bash -e
        source fastx_toolkit-0.0.13.2
        
        gzip -cd {R1_in} | fastx_trimmer -f14 -z -o {R1_out} -Q33
        gzip -cd {R2_in} | fastx_trimmer -f14 -z -o {R2_out} -Q33         

        '''.format(R1_in=self.input()[0].path,
                   R2_in=self.input()[1].path,
                   R1_out=self.output()[0].path,
                   R2_out=self.output()[1].path)

@requires(PythonFilter)
class Star(SlurmExecutableTask):
    '''Runs STAR to align to the reference :param str star_genome:'''
    star_genome = luigi.Parameter()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 12000
        self.n_cpu = 4
        self.partition = "tgac-medium"
    
    def output(self):        
        return {
            'star_sam' : LocalTarget(os.path.join(self.scratch_dir, self.library, 'Aligned.out.sam')),
            'star_log' : LocalTarget(os.path.join(self.base_dir, self.library, 'Log.final.out'))
        }
    
    def work_script(self):
        return '''#!/bin/bash -e
                  source star-2.5.0a
                  cd {scratch_dir}
                  STAR  --genomeDir {star_genome} -runThreadN {n_cpu} --readFilesCommand gunzip -c --readFilesIn {R1} {R2}
                  cp {scratch_dir}/Log.final.out {working_dir}/Log.final.out
                  '''.format(working_dir=os.path.join(self.base_dir, self.library),
                             scratch_dir=os.path.join(self.scratch_dir, self.library),
                             star_genome=self.star_genome, 
                             n_cpu=self.n_cpu,
                             R1=self.input()[0].path,
                             R2=self.input()[1].path,)

@requires(Star)
class CleanSam(SlurmExecutableTask):
    '''Cleans the provided SAM/BAM, soft-clipping beyond-end-of-reference alignments and setting MAPQ to 0 for unmapped reads'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.scratch_dir, self.library, 'Aligned.out_cleaned.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source picardtools-2.1.1
               picard='{picard}'
               $picard CleanSam VERBOSITY=ERROR QUIET=true I={input} O={output}
                '''.format(input=self.input()['star_sam'].path, 
                           output=self.output().path,
                           picard=picard.format(mem=self.mem))

@requires(CleanSam)
class AddReadGroups(SlurmExecutableTask):
    '''Sets the read group to the sample name, required for GATK'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        working_dir = os.path.join(self.base_dir, self.library)
        return LocalTarget(os.path.join(self.scratch_dir, self.library, 'rg_added_sorted.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source picardtools-2.1.1
               picard='{picard}' 
               $picard AddOrReplaceReadGroups VERBOSITY=ERROR QUIET=true I={input} O={output} SO=coordinate RGID=Star RGLB={lib} RGPL=Ilumina RGPU=Ilumina RGSM={lib} 
                '''.format(input=self.input().path, 
                           output=self.output().path,
                           lib=self.library,
                           picard=picard.format(mem=self.mem))

@requires(AddReadGroups)
class MarkDuplicates(SlurmExecutableTask):
    '''Marks optical/PCR duplicates'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, self.library, 'dedupped.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source picardtools-2.1.1
               picard='{picard}'
               $picard MarkDuplicates VERBOSITY=ERROR QUIET=true I={input} O={output} CREATE_INDEX=true VALIDATION_STRINGENCY=SILENT M=/dev/null
                '''.format(input=self.input().path, 
                           output=self.output().path,
                           picard=picard.format(mem=self.mem))

@requires(MarkDuplicates)
class BaseQualityScoreRecalibration(SlurmExecutableTask):
    '''Runs BQSR. Because this requires a set of high quality SNPs to use
    as a ground truth we bootstrap this by first running the pipeline without
    BQSR then running again using the best SNPs of the first run.
    
    This is achieved by conditionally overriding run() on whether a snp_db is given 
    '''
    snp_db = luigi.Parameter(default='')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 8000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
            
    def output(self):
        if self.snp_db == '':
            return LocalTarget(os.path.join(self.base_dir, self.library, 'dedupped.bam'))
        else:
            return LocalTarget(os.path.join(self.base_dir, self.library, 'recalibrated.bam'))
            
    def run(self):
        if self.snp_db == '':
            logger.info("Not running BQSR as no snp_db given")
        else:
            logger.info("Running BQSR recalibration using bootstrapped snp_db " +  self.snp_db)
            super(type(self), self).run()
            
    def work_script(self):
        recal = os.path.join(self.base_dir, self.library, self.library+"_recal.tsv")
        return '''#!/bin/bash -e
                  source jre-8u92
                  source gatk-3.6.0
                  gatk='{gatk}'
                  $gatk -T BaseRecalibrator  -R {reference}  -I {input}  -knownSites {snp_db}  -o {recal}
                  $gatk -T PrintReads -R {reference} -I {input} -BQSR {recal} -o {output}
                '''.format(gatk=gatk.format(mem=self.mem),
                           input=self.input().path,
                           output=self.output().path,
                           reference=self.reference,
                           recal=recal)

@requires(BaseQualityScoreRecalibration)
class SplitNCigarReads(SlurmExecutableTask):
    '''Required by GATK, breaks up reads spanning introns'''
    reference = luigi.Parameter()
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 1000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.scratch_dir, self.library, 'split.bam'))
    
    def work_script(self):
        return '''#!/bin/bash -e
               source jre-8u92
               source gatk-3.6.0
               gatk='{gatk}'
               $gatk -T SplitNCigarReads --logging_level ERROR -R {reference} -I {input} -o {output} -rf ReassignOneMappingQuality -RMQF 255 -RMQT 60 -U ALLOW_N_CIGAR_READS
                '''.format(input=self.input().path, 
                           output=self.output().path,
                           gatk=gatk.format(mem=self.mem),
                           reference=self.reference) 

@requires(SplitNCigarReads)
class HaplotypeCaller(SlurmExecutableTask):
    '''Per sample SNP calling'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 6000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, self.library, self.library + ".g.vcf"))
        
    def work_script(self):
        return '''#!/bin/bash -e
                source jre-8u92
                source gatk-3.6.0
                gatk='{gatk}'
                $gatk -T HaplotypeCaller --logging_level ERROR -R {reference} -I {input} -dontUseSoftClippedBases --emitRefConfidence GVCF -o {output}
        '''.format(input=self.input().path, 
                   output=self.output().path,
                   gatk=gatk.format(mem=self.mem),
                   reference=self.reference) 

@requires(HaplotypeCaller)
class PlotAlleleFreq(SlurmExecutableTask):
    '''Make plots of the ranked allele frequencies to identify mixed isolates'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 1
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, self.library, 'QC', self.library + "_allele_freqs.pdf"))
        
    def work_script(self):
        self.temp1=TemporaryFile()
        self.temp2=TemporaryFile()
        return '''#!/bin/bash -e
                source jre-8u92
                {python}
                source gatk-3.6.0
                gatk='{gatk}'
                
                $gatk -T VariantsToTable -R {reference} -AMD -V {input} -F CHROM -F POS -F REF -F ALT -F DP -GF AD  --out {temp1}
                grep -ve "NA" <  {temp1}  > {temp2}

                python plotAF.py {temp2} {output}
                
                '''.format(python=python,
                            gatk=gatk.format(mem=self.mem),
                            reference=self.reference,
                            input=self.input().path,
                            output=self.output().path,
                            temp1=self.temp1.path,
                            temp2=self.temp2.path)

@inherits(SplitNCigarReads)
@inherits(FastxQC)
@inherits(PlotAlleleFreq)
class PerLibPipeline(luigi.WrapperTask):
    '''Wrapper task that runs all tasks on a single library'''
    def requires(self):
        yield self.clone(FastxQC)
        yield self.clone(HaplotypeCaller)
        yield self.clone(PlotAlleleFreq)

#-----------------------------------------------------------------------#
@inherits(PerLibPipeline)        
class LibraryBatchWrapper(luigi.WrapperTask):
    '''Wrapper task to execute the per library part of the pipline on all
        libraries in :param list lib_list:'''
    lib_list = luigi.ListParameter()        
    library=None
    def requires(self):
        print(self.lib_list)
        for lib in self.lib_list:
            yield self.clone_parent(library=lib.rstrip())
# This is a bit of a hack, it allows us to pass parameters to LibraryBatchWrapper and have them propagate
# down to all calls to PerLibPipeline.
LibraryBatchWrapper.library=None

@requires(LibraryBatchWrapper)        
class GenotypeGVCF(SlurmExecutableTask):
    '''Combine the per sample g.vcfs into a complete callset
    :param str output_prefix: '''
    output_prefix = luigi.Parameter(default="genotypes")
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 32000
        self.n_cpu = 1
        self.partition = "tgac-medium"
    
    def input(self):
        ## THIS IS A MASSIVE FAT HACK
        return  [LocalTarget(os.path.join(self.base_dir, library, library + ".g.vcf")) for library in self.lib_list]
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'callsets', self.output_prefix, self.output_prefix+"_raw.vcf.gz"))
        
    def work_script(self):
        return ('''#!/bin/bash -e
                source jre-8u92
                source gatk-3.6.0
                gatk='{gatk}'
                $gatk -T GenotypeGVCFs --logging_level ERROR -R {reference} -o {output} --includeNonVariantSites '''.format(output=self.output().path,
                           gatk=gatk.format(mem=self.mem),
                           reference=self.reference)+
                "\n".join(["--variant "+ lib.path +" \\" for lib in self.input()]))

@requires(GenotypeGVCF)
class VcfToolsFilter(SlurmExecutableTask):
    '''Applies hard filtering to the raw callset'''
    GQ = luigi.IntParameter(default=30)
    QD = luigi.IntParameter(default=5)
    FS = luigi.IntParameter(default=30)
    mask = luigi.Parameter(default="PST130_RNASeq_collapsed_exons.bed")
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 8000
        self.n_cpu = 2
        self.partition = "tgac-medium"
        
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'callsets', self.output_prefix , self.output_prefix + "_filtered.vcf.gz"))
    
    def work_script(self):
        self.temp1 = TemporaryFile()
        self.temp2 = TemporaryFile()
        
        return '''#!/bin/bash -e
                source vcftools-0.1.13;
                source bcftools-1.3.1;
                
                bcftools view --apply-filters . {input} -o {temp1} -O z --threads 1
                bcftools filter {temp1} -e "FMT/RGQ < {GQ} || FMT/GQ < {GQ} || QD < {QD} || FS > {FS}" --set-GTs . -o {temp2} -O z --threads 1
                vcftools --gzvcf {temp2} --recode --max-missing 0.000001 --stdout --bed {mask} | bgzip -c > {output}
                tabix -p vcf {output}
                '''.format(input=self.input().path,
                           output=self.output().path,
                           GQ=self.GQ,
                           QD=self.QD,
                           FS=self.FS,
                           mask=self.mask,
                           temp1=self.temp1.path,
                           temp2=self.temp2.path)

@requires(VcfToolsFilter)
class GetSNPs(SlurmExecutableTask):
    '''Extracts just sites with only biallelic SNPs that have a least one variant isolate'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 1
        self.partition = "tgac-medium"
    
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'callsets', self.output_prefix , self.output_prefix + "_SNPs_only.vcf.gz"))
        
    def work_script(self):
        return '''#!/bin/bash -e
                  source jre-8u92
                  source gatk-3.6.0
                  gatk='{gatk}'
                  $gatk -T -T SelectVariants -V {input} -R {reference} --restrictAllelesTo BIALLELIC --selectTypeToInclude SNP --out {output}
                  '''.format(input=self.input().path,
                             output=self.output().path,
                             reference=self.reference,
                             gatk=gatk.format(mem=self.mem))

@requires(VcfToolsFilter)
class GetINDELs(SlurmExecutableTask):
    '''Get sites with MNPs'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 1
        self.partition = "tgac-medium"
    
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'callsets', self.output_prefix , self.output_prefix + "_INDELs_only.vcf.gz"))
        
    def work_script(self):
        return '''#!/bin/bash -e
                  source jre-8u92
                  source gatk-3.6.0
                  gatk='{gatk}'
                  $gatk -T -T SelectVariants -V {input} -R {reference} --selectTypeToInclude MNP  --selectTypeToInclude MIXED  --out {output}
                  '''.format(input=self.input().path,
                             output=self.output().path,
                             reference=self.reference,
                             gatk=gatk.format(mem=self.mem))

@requires(VcfToolsFilter)
class GetRefSNPSs(SlurmExecutableTask):
    '''Create a VCF with SNPs and include sites that are reference like in all samples'''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set the SLURM request params for this task
        self.mem = 4000
        self.n_cpu = 1
        self.partition = "tgac-medium"
    
    def output(self):
        return LocalTarget(os.path.join(self.base_dir, 'callsets', self.output_prefix , self.output_prefix + "_RefSNPs.vcf.gz"))
        
    def work_script(self):
        return '''#!/bin/bash -e
                  source jre-8u92
                  source gatk-3.6.0
                  gatk='{gatk}'
                  $gatk -T -T SelectVariants -V {input} -R {reference} --restrictAllelesTo BIALLELIC --selectTypeToInclude SYMBOLIC --selectTypeToInclude NO_VARIATION  --selectTypeToInclude SNP --out {output}
                  '''.format(input=self.input().path,
                             output=self.output().path,
                             reference=self.reference,
                             gatk=gatk.format(mem=self.mem))

#-----------------------------------------------------------------------#

@inherits(GetSNPs)
@inherits(GetINDELs)
@inherits(GetRefSNPSs)
class SnpCalling(luigi.WrapperTask):
    def requires(self):
        yield self.clone(GetSNPs)
        yield self.clone(GetINDELs)
        yield self.clone(GetRefSNPSs)


if __name__ == '__main__':
    os.environ['TMPDIR'] = "/tgac/scratch/buntingd"
    
    with open(sys.argv[1], 'r') as libs_file:
        lib_list = [line.rstrip() for line in libs_file]
        
    luigi.run(['SnpCalling', '--lib-list', json.dumps(lib_list),
                               '--star-genome', '/tgac/workarea/collaborators/saunderslab/Realignment/data/genome/',
                               '--reference', '/tgac/workarea/collaborators/saunderslab/Realignment/data/PST130_contigs.fasta',
                               '--mask', '/tgac/workarea/users/buntingd/realignment/PST130/Combined/PST130_RNASeq_collapsed_exons.bed',
                               '--workers', '3',])
                               